"""Invariants this repository must hold on Windows, macOS and Linux.

None of these need the emulator, Docker, or the fixture wheels — they are about
the repository itself, so they are the part of CI that is green from day one and
runs identically on all three platforms.
"""

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
# Every top-level module the fixture wheels provide, kept out of uv.lock on
# purpose: which release they came from is the thing under test. Read off the
# wheels' own RECORD files (contoso_fixtures, contoso_fixtures_advanced,
# fabric-target) rather than guessed, and listed here because a clean checkout
# — the case this guard exists for — has no wheel to ask.
WHEELS = frozenset(
    {
        "fabric_target",
        "common",
        "source_system",
        "erp_system",
        "reference_data",
        "web_store",
    }
)


def _pins():
    out = {}
    for line in (ROOT / "versions.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def test_every_image_is_pinned_to_a_version():
    """All THREE emulator images.

    The family ships on independent cadences, so fabric-emulator, entra and
    keyvault sit on different version lines and one pin cannot describe the
    stack. Assuming it could is how this repo first failed to start: `manifest
    unknown`, because 0.13.0 existed for one image and not the others.

    MOKAPI IS NO LONGER HERE, and that is the point rather than an omission.
    The simulator is part of what "the vendor" means, so it is pinned by
    `contoso-sources` alongside the specs it serves — two consumers on
    different mokapis are not pulling from the same vendor even if the specs
    match. `scripts/sources.py` reads that repo's versions.env and refuses to
    guess a version it does not find there.
    """
    pins = _pins()
    expected = {
        "FABRIC_EMULATOR_VERSION",
        "ENTRA_EMULATOR_VERSION",
        "KEYVAULT_EMULATOR_VERSION",
    }
    assert expected <= set(pins), expected - set(pins)
    # The invariant is IMMUTABLE, not a particular shape. Upstream projects
    # version how they like — postgres `16.4`, redpanda `v24.2.7`, debezium
    # `2.7.3.Final` — and demanding X.Y.Z of all of them would say nothing
    # about reproducibility while rejecting perfectly good pins.
    mutable = {"latest", "stable", "main", "edge", "nightly", "dev", "alpha"}
    for k, v in pins.items():
        assert v, f"{k} is empty"
        assert v.lower() not in mutable, f"{k}={v} is a moving tag"
        assert any(c.isdigit() for c in v), f"{k}={v} names no version"


def test_compose_reads_every_pin():
    """A pin nothing substitutes is a comment. Each variable must appear in a
    compose file, or the image silently falls back to whatever is there."""
    composed = "".join(
        p.read_text(encoding="utf-8") for p in (ROOT / "compose").glob("*.yml")
    )
    for k in _pins():
        assert "${" + k in composed, f"{k} is pinned but never used"


def test_compose_never_uses_latest():
    # `latest` would make a green run unattributable: something worked, but you
    # could not say which release.
    text = (ROOT / "compose" / "docker-compose.yml").read_text(encoding="utf-8")
    assert ":latest" not in text
    assert "${FABRIC_EMULATOR_VERSION" in text


def test_every_make_recipe_survives_cmd_exe():
    """The cross-platform claim, enforced.

    GNU Make on Windows runs recipes through cmd.exe. A recipe using a pipe, a
    shell builtin, `rm`, backticks or `&&` works on two platforms and fails on
    the third — and it fails for the user, not for us, which is the wrong place
    to find out. Logic belongs in scripts/, which is Python.
    """
    banned = re.compile(
        r"(\|\||&&|\|(?!\|)|`|\brm\b|\bcp\b|\bmv\b|\bcat\b|"
        r"\bsed\b|\btest\b\s+-|\bif\b\s|\bfor\b\s|\$\(shell)"
    )
    offenders = []
    for line in MAKEFILE.splitlines():
        if not line.startswith("\t"):
            continue
        recipe = line.lstrip("\t").lstrip("@")
        if banned.search(recipe):
            offenders.append(recipe)
    assert not offenders, f"these recipes would not run on cmd.exe: {offenders}"


def test_make_targets_are_documented():
    # `make help` is generated from these, so an undocumented target is an
    # invisible one.
    declared = set(re.findall(r"^\.PHONY:\s*(.+)$", MAKEFILE, re.M)[0].split())
    documented = set(re.findall(r"^([a-z][a-z0-9-]*):.*?##", MAKEFILE, re.M))
    assert declared == documented, declared ^ documented


def test_the_emulator_client_plumbing_is_never_imported():
    """This repo must build against the emulator like any consumer would.

    `common.py` ships inside contoso-fixtures — it is the in-tree examples'
    client plumbing (endpoints, token minting, tds_connect). Importing it here
    would hand this repository the answer key and quietly void the one claim it
    exists to make: that the published emulator is usable by someone who does
    not have its source.
    """
    offenders = []
    for p in ROOT.rglob("*.py"):
        if ".venv" in p.parts or p.name == "test_repo.py":
            continue
        src = p.read_text(encoding="utf-8")
        if re.search(r"^\s*(from common import|import common\b)", src, re.M):
            offenders.append(p.relative_to(ROOT).as_posix())
    assert not offenders, f"must not import the emulator's own plumbing: {offenders}"


def test_python_is_only_ever_invoked_through_uv():
    """uv, strictly.

    A bare `python` or `pip` in a recipe or workflow resolves to whatever the
    machine happens to have — a different interpreter on Windows than on the
    Linux runner, and a different one again on a contributor's Mac. The whole
    point of committing uv.lock is that those are the same.
    """
    bad = []
    files = [ROOT / "Makefile", *sorted((ROOT / ".github/workflows").glob("*.yml"))]
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip().lstrip("@- ")
            if stripped.startswith("#") or "python-version" in stripped:
                continue
            if re.match(r"^(run:\s*)?(python3?|pip3?)\s", stripped):
                bad.append(f"{f.name}:{i}: {stripped}")
    assert not bad, f"invoke through uv instead: {bad}"


def test_the_lockfile_is_committed():
    """Without it, `--frozen` has nothing to be frozen to and three platforms
    resolve three different dependency sets."""
    assert (ROOT / "uv.lock").exists()
    assert "pytest" in (ROOT / "uv.lock").read_text(encoding="utf-8"), (
        "dev group not locked"
    )


def test_every_rule_names_a_test_that_exists():
    """RULES.md is the codebase's rules. A rule citing a test that does not
    exist is prose asserting a guarantee nothing enforces — the failure this
    whole platform is built to catch, turned on our own documentation.

    `judgement` is an honest answer and is allowed. A wrong test name is not.
    """
    rules = (ROOT / "RULES.md").read_text(encoding="utf-8")
    cited = set(re.findall(r"`(test_[a-z0-9_]+)`", rules))
    assert cited, "RULES.md cites no tests at all"

    defined = set()
    for p in (ROOT / "tests").glob("test_*.py"):
        defined |= set(
            re.findall(r"^def (test_[a-z0-9_]+)", p.read_text(encoding="utf-8"), re.M)
        )

    missing = sorted(cited - defined)
    assert not missing, f"RULES.md cites tests that do not exist: {missing}"


def test_set_release_moves_every_version_the_emulator_tags():
    """The emulator's release tags fabric-emulator, sail AND spark-agent.

    Sail is the Spark engine — bronze and silver run inside it. spark-agent is
    what the emulator drives to execute a notebook. Moving the emulator while
    leaving either pinned would verify a new release against an old engine and
    call the result a release test.

    NOTE this test cannot catch an allowlist that has fallen BEHIND; it derives
    its expectation from the allowlist. That is
    `test_every_emulator_family_image_tracks_the_release`'s job.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    from set_release import TRACKS_THE_RELEASE, set_version

    text = (ROOT / "versions.env").read_text(encoding="utf-8")
    new, moved = set_version(text, "9.9.9")
    assert set(moved) == set(TRACKS_THE_RELEASE), moved
    for key in TRACKS_THE_RELEASE:
        assert re.search(rf"^{key}=9\.9\.9$", new, re.M), key
    # Versions on independent cadences must NOT be dragged along.
    independent = (
        "ENTRA_EMULATOR_VERSION",
        "KEYVAULT_EMULATOR_VERSION",
    )
    for key in independent:
        b = re.search(rf"^{key}=(.+)$", text, re.M)
        a = re.search(rf"^{key}=(.+)$", new, re.M)
        assert b and a, f"{key} is missing from versions.env"
        assert b.group(1) == a.group(1), f"{key} moved: {b.group(1)} -> {a.group(1)}"


def test_every_emulator_family_image_tracks_the_release():
    """Check the allowlist against something OTHER than itself.

    `test_set_release_moves_every_version_the_emulator_tags` asserts
    `set(moved) == set(TRACKS_THE_RELEASE)` — it derives its expectation FROM
    the allowlist, so it passes whatever that tuple happens to say, including a
    tuple that has silently fallen behind. Add a fourth image published by the
    emulator's release workflow, forget the allowlist, and it stays pinned at
    the previous version while everything above stays green. That already
    nearly happened: spark-agent arrived and the allowlist named two things.

    Compose is the independent witness. Every `ghcr.io/calvinchengx/
    fabric-emulator*` image is tagged `{{version}}` by that one release
    workflow, so its version variable MUST move with the release. entra and
    keyvault are separate repositories on their own cadences, and the prefix
    is what tells them apart.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    from set_release import TRACKS_THE_RELEASE

    composed = "".join(
        p.read_text(encoding="utf-8") for p in sorted((ROOT / "compose").glob("*.yml"))
    )
    # A SET of pairs, not a dict keyed by image. A dict lets the LAST file win,
    # so an overlay naming the same image erases the base pin from what this
    # guard witnesses — it would then be checking the overlay and reporting on
    # the release. Found when compose/terminal.yml pinned the family to
    # unreleased builds and the base FABRIC_EMULATOR_VERSION vanished from the
    # evidence entirely.
    pins = set(
        re.findall(
            r"image:\s*ghcr\.io/calvinchengx/(fabric-emulator[\w-]*)"
            r":\$\{([A-Z_]+)",
            composed,
        )
    )
    assert pins, (
        "no ghcr.io/calvinchengx/fabric-emulator* images found in compose — "
        "this guard is reading the wrong thing and would pass on anything"
    )
    # An overlay may deliberately pin an image to something the release does not
    # move — that is what running an unreleased build MEANS, and refusing it
    # would make the guard forbid the one job overlays exist for.
    #
    # It is exempt only while the image ALSO carries a tracked pin somewhere, so
    # the override can add a way to escape the release but never become the only
    # pin. Drop the base and this fails, which is the case worth catching: a
    # stack that has quietly stopped following releases at all.
    tracked = {img for img, var in pins if var in TRACKS_THE_RELEASE}
    missing = {
        img: var
        for img, var in pins
        if var not in TRACKS_THE_RELEASE
        and not (var.endswith("_OVERRIDE") and img in tracked)
    }
    assert not missing, (
        f"published by the emulator's release but NOT in TRACKS_THE_RELEASE: "
        f"{missing}. A release would move the emulator and leave these behind, "
        f"silently — add them to scripts/set_release.py. (A deliberate "
        f"unreleased pin must be named <VAR>_OVERRIDE and the image must keep "
        f"its tracked pin in the base compose.)"
    )


def test_set_release_refuses_a_payload_that_is_not_a_version():
    """An empty client_payload would otherwise write `VERSION=` and surface
    four steps later as an image-pull error naming neither the payload nor this
    script."""
    for bad in ("", "latest", "v", "0.13", "; rm -rf /"):
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "set_release.py"), bad],
            capture_output=True,
            text=True,
        )
        assert r.returncode != 0, f"accepted {bad!r}"


def test_the_acceptance_run_uses_the_dispatched_version():
    """A dispatch that triggers a run against the OLD pin is worse than no
    dispatch: it reports success for a release nobody tested."""
    wf = (ROOT / ".github" / "workflows" / "acceptance.yml").read_text(encoding="utf-8")
    assert "repository_dispatch" in wf
    assert "client_payload.version" in wf, (
        "acceptance is triggered by a release but never reads which one"
    )
    assert "set_release.py" in wf


def test_the_pin_moves_only_after_a_green_verify():
    """Adoption is automatic, so the GATE is the whole safety argument.

    The acceptance run commits the dispatched version back to versions.env,
    which means a released emulator becomes the one this platform claims to
    support without a human in the loop. That is only sound while the commit
    is unreachable from a failed run: an `if: always()` here, or the step
    drifting above `make verify`, would adopt a version precisely when the
    evidence says not to — and it would do it silently, in the emulator's own
    release history.
    """
    import yaml

    wf = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "acceptance.yml").read_text(encoding="utf-8")
    )
    job = wf["jobs"]["verify"]
    steps = job["steps"]

    def index_of(pred) -> int:
        hits = [i for i, s in enumerate(steps) if pred(s)]
        assert len(hits) == 1, f"expected exactly one matching step, got {hits}"
        return hits[0]

    # `startswith`, not equality: the step carries `PRODUCT=...` now that the
    # product is a separate repository, and an exact match would silently find
    # nothing -- which reads as "there is no verify step" rather than "the
    # matcher is stale". The `make verify` prefix is what this test is about.
    verify = index_of(lambda s: s.get("run", "").strip().startswith("make verify"))
    adopt = index_of(lambda s: "push origin" in s.get("run", ""))

    assert adopt > verify, "the pin is adopted before the run that verifies it"

    cond = str(steps[adopt].get("if", ""))
    assert "always()" not in cond, (
        "the adopt step runs even when verification failed; "
        "a red run must leave the pin where it is"
    )
    assert "repository_dispatch" in cond, (
        "adoption must be scoped to a release dispatch — the schedule verifies "
        "the EXISTING pin and has nothing to adopt"
    )

    # Writing to the repository is not the default and must be asked for
    # explicitly, or the push fails at the end of an eight-minute run.
    assert job.get("permissions", {}).get("contents") == "write"
