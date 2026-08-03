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
MAKEFILE = (ROOT / "Makefile").read_text()


def _pins():
    out = {}
    for line in (ROOT / "versions.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def test_every_image_is_pinned_to_a_version():
    """All THREE emulator images plus mokapi.

    The family ships on independent cadences, so fabric-emulator, entra and
    keyvault sit on different version lines and one pin cannot describe the
    stack. Assuming it could is how this repo first failed to start: `manifest
    unknown`, because 0.13.0 existed for one image and not the others.
    """
    pins = _pins()
    expected = {
        "FABRIC_EMULATOR_VERSION",
        "ENTRA_EMULATOR_VERSION",
        "KEYVAULT_EMULATOR_VERSION",
        "MOKAPI_VERSION",
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
    composed = "".join(p.read_text() for p in (ROOT / "compose").glob("*.yml"))
    for k in _pins():
        assert "${" + k in composed, f"{k} is pinned but never used"


def test_compose_never_uses_latest():
    # `latest` would make a green run unattributable: something worked, but you
    # could not say which release.
    text = (ROOT / "compose" / "docker-compose.yml").read_text()
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
        src = p.read_text()
        if re.search(r"^\s*(from common import|import common\b)", src, re.M):
            offenders.append(p.relative_to(ROOT).as_posix())
    assert not offenders, f"must not import the emulator's own plumbing: {offenders}"


def test_the_toggle_contract_is_installed_not_restated():
    """`fabric-target` is the FABRIC_TARGET contract, published by the emulator
    and installed by `make fixtures`. This repo consumes it.

    It used to restate it, and the restatement drifted: the real target
    resolved an Entra client-credentials flow and required AZURE_CLIENT_SECRET,
    so `az login` could not drive the platform, a managed identity could not,
    and it could not have run inside a Fabric notebook at all — there is no
    client secret to give there. A copied contract is a contract that gets one
    branch wrong and stays green, because the emulator does not care which
    identity showed up.
    """
    src = (ROOT / "platform" / "target.py").read_text()
    assert "import fabric_target" in src, (
        "target.py must consume the published contract, not restate it"
    )
    assert "grant_type" not in src, "the grant type is the package's business"

    # And nowhere else may mint a token by hand. Matched on the CODE shape — a
    # quoted `grant_type` key, or the token endpoint — rather than on the bare
    # words, which appear in the prose explaining why this rule exists.
    hand_rolled = re.compile(r"""["']grant_type["']|oauth2/v2\.0/token""")
    offenders = []
    for p in sorted((ROOT / "platform").glob("*.py")):
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if hand_rolled.search(line):
                offenders.append(f"{p.name}:{i}: {line.strip()}")
    assert not offenders, (
        "tokens come from the target's credential, so that az login, a service "
        f"principal and a notebook's managed identity all work: {offenders}"
    )


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
        for i, line in enumerate(f.read_text().splitlines(), 1):
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
    assert "pytest" in (ROOT / "uv.lock").read_text(), "dev group not locked"


def test_the_emulator_appears_only_in_the_target_resolver():
    """This platform must run unmodified against real Fabric.

    Every difference between the local family and production lives in
    platform/target.py, selected by FABRIC_TARGET. A seeded credential, a
    localhost URL or a TLS bypass anywhere else is a workaround that would ship
    to production — and would be invisible, because the emulator would go on
    passing.
    """
    emulator_only = re.compile(
        r"localhost:9443|localhost:8443|daemon-app-secret|cccccccc-0000|"
        r"11111111-1111|allow_invalid_certificates|verify\s*=\s*False"
    )
    offenders = []
    for p in sorted((ROOT / "platform").glob("*.py")):
        if p.name == "target.py":
            continue
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if emulator_only.search(line):
                offenders.append(f"{p.name}:{i}: {line.strip()}")
    assert not offenders, "these belong in target.py, behind FABRIC_TARGET: " + str(
        offenders
    )


def real_branch() -> str:
    """The real target's arm of the resolver, as source.

    Read rather than executed: constructing the real target needs a live
    credential source, and the point of these assertions is that the value is a
    LITERAL no configuration can reach — which is a property of the text.
    """
    src = (ROOT / "platform" / "target.py").read_text()
    return src[
        src.index("if ft.is_real:") : src.index("return Target(\n        name=EMULATOR")
    ]


def test_tls_verification_is_never_hardcoded_off():
    """The real target must not be able to run with verification disabled."""
    real = real_branch()
    assert "verify_tls=True" in real, "the real target must verify TLS"
    assert "allow_invalid" not in real


def test_the_fabric_client_knows_nothing_about_the_source_systems():
    """Segregation, in the other direction.

    Contoso POS, Web and ERP are vendors a Fabric pipeline pulls from — not
    Fabric. A Fabric client that knows what a vendor's DSN looks like has mixed
    two things that change for different reasons and at different times.
    """
    src = (ROOT / "platform" / "fabric.py").read_text()
    for leak in ("POS_", "ERP_", "DEBEZIUM", "REDPANDA", "postgresql://"):
        assert leak not in src, f"fabric.py should not mention {leak}"


def test_the_transforms_are_engine_side():
    """bronze and silver must scale.

    They run in the engine — Spark reading OneLake directly — so the same code
    holds at a hundred million rows. A client-side dataframe library here would
    put one machine in the data path and cap the platform at its memory, which
    is exactly what a single-node engine quietly does.
    """
    for name in ("bronze.py", "silver.py"):
        src = (ROOT / "platform" / name).read_text()
        assert "import spark" in src, f"{name} does not use the engine"
        for single_node in ("duckdb", "pandas", "deltalake", "pyarrow"):
            assert single_node not in src, (
                f"{name} pulls data client-side with {single_node} — the "
                f"transforms must stay in the engine to scale"
            )


def test_every_rule_names_a_test_that_exists():
    """RULES.md is the codebase's rules. A rule citing a test that does not
    exist is prose asserting a guarantee nothing enforces — the failure this
    whole platform is built to catch, turned on our own documentation.

    `judgement` is an honest answer and is allowed. A wrong test name is not.
    """
    rules = (ROOT / "RULES.md").read_text()
    cited = set(re.findall(r"`(test_[a-z0-9_]+)`", rules))
    assert cited, "RULES.md cites no tests at all"

    defined = set()
    for p in (ROOT / "tests").glob("test_*.py"):
        defined |= set(re.findall(r"^def (test_[a-z0-9_]+)", p.read_text(), re.M))

    missing = sorted(cited - defined)
    assert not missing, f"RULES.md cites tests that do not exist: {missing}"


def test_credentials_come_from_key_vault():
    """Secrets live in Key Vault — the emulator's locally, a real Azure Key
    Vault in production — never in the source tree.

    A credential in a repository has already leaked: it is in every clone, in
    the reflog, and in whatever CI cached the checkout. It also skips the part
    a real deployment must get right — an identity permitted to read a vault,
    and rotation without a code change.

    Two exceptions, both structural:
      * target.py holds the BOOTSTRAP Entra credential — reading the vault
        requires it, so it cannot live there.
      * seed_secrets.py is the one place a value appears, because a clone has
        to be self-contained. It does not run against real vendors.
    """
    allowed = {"target.py", "seed_secrets.py"}
    pattern = re.compile(r"(password|secret|api[_-]?key)\s*=\s*[\"'][^\"']{6,}", re.I)
    offenders = []
    for p in sorted((ROOT / "platform").glob("*.py")):
        if p.name in allowed:
            continue
        for i, line in enumerate(p.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "_SECRET" in stripped:
                continue
            if pattern.search(stripped):
                offenders.append(f"{p.name}:{i}: {stripped}")
    assert not offenders, f"read these from the vault instead: {offenders}"


def test_set_release_moves_every_version_the_emulator_tags():
    """The emulator's release tags fabric-emulator AND sail together.

    Sail is the Spark engine — bronze and silver run inside it — so moving the
    emulator while leaving sail pinned would verify a new release against an
    old engine and call the result a release test.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    from set_release import TRACKS_THE_RELEASE, set_version

    text = (ROOT / "versions.env").read_text()
    new, moved = set_version(text, "9.9.9")
    assert set(moved) == set(TRACKS_THE_RELEASE), moved
    for key in TRACKS_THE_RELEASE:
        assert re.search(rf"^{key}=9\.9\.9$", new, re.M), key
    # Versions on independent cadences must NOT be dragged along.
    independent = (
        "ENTRA_EMULATOR_VERSION",
        "KEYVAULT_EMULATOR_VERSION",
        "MOKAPI_VERSION",
    )
    for key in independent:
        b = re.search(rf"^{key}=(.+)$", text, re.M)
        a = re.search(rf"^{key}=(.+)$", new, re.M)
        assert b and a, f"{key} is missing from versions.env"
        assert b.group(1) == a.group(1), f"{key} moved: {b.group(1)} -> {a.group(1)}"


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
    wf = (ROOT / ".github" / "workflows" / "acceptance.yml").read_text()
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

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "acceptance.yml").read_text())
    job = wf["jobs"]["verify"]
    steps = job["steps"]

    def index_of(pred) -> int:
        hits = [i for i, s in enumerate(steps) if pred(s)]
        assert len(hits) == 1, f"expected exactly one matching step, got {hits}"
        return hits[0]

    verify = index_of(lambda s: s.get("run", "").strip() == "make verify")
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
