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
    # Named per layer, because the two get their session from different places
    # and both are correct. bronze is a script and calls spark.session(); silver
    # is a Fabric notebook, where `spark` is ambient and building a second
    # session is the thing the rule exists to prevent. What must hold for both
    # is that the rows never leave the engine.
    transforms = {
        "bronze.py": "import spark",
        "silver_notebook.py": "spark.read",
    }
    for name, uses_engine in transforms.items():
        src = (ROOT / "platform" / name).read_text()
        assert uses_engine in src, f"{name} does not use the engine"
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


def test_silver_runs_as_a_fabric_notebook():
    """The transform is notebook code, not code that resembles it.

    `spark.py` claims the transforms are paste-able into a Fabric notebook. A
    claim like that is worth nothing until something runs it as one — so silver
    publishes a Notebook item and submits a RunNotebook job, and the notebook's
    source is a REAL FILE rather than a string assembled at publish time.

    The file matters as much as the job. A notebook built from an f-string is
    not Python as far as any tool is concerned: ruff does not lint it, ty does
    not check it, and a syntax error in the transform surfaces as a failed cell
    on a remote engine instead of at `make lint`.
    """
    nb = ROOT / "platform" / "silver_notebook.py"
    assert nb.exists(), "the silver transform is not a notebook file"
    assert nb.read_text().startswith("# Fabric notebook source"), (
        "a Fabric notebook is identified by its first line; without it the "
        "emulator's parser sees one unmarked cell rather than the cells written"
    )
    assert "# CELL " in nb.read_text(), "the notebook declares no cells"

    src = (ROOT / "platform" / "silver.py").read_text()
    assert "jobType=RunNotebook" in src, "silver never submits a notebook job"
    assert "SOURCE.read_text()" in src, (
        "silver must publish the notebook FILE — a body built inline here is "
        "the thing this test exists to prevent"
    )


def test_the_engine_driver_never_runs_against_real_fabric():
    """Playing the Spark pool is emulator scaffolding.

    Real Fabric schedules a RunNotebook job onto its own pool; the emulator
    parses the notebook and waits for an engine to report, so locally the
    platform supplies one. That difference is resolved by the target like every
    other, and an engine driver that ran against production would execute the
    notebook twice — once here and once on Fabric's pool.
    """
    real = real_branch()
    assert "runs_notebooks_itself=True" in real, (
        "real Fabric runs its own notebooks; the platform must not"
    )

    src = (ROOT / "platform" / "silver.py").read_text()
    assert "if not T.runs_notebooks_itself:" in src, (
        "the engine driver is not gated on the target"
    )


def test_notebook_lineage_is_observed_not_declared():
    """An edge records what happened. Nothing else is allowed to invent one.

    The first version of this had the publishing step declare one read set and
    one write set for the whole notebook. The emulator paired every read with
    every write — correctly, given what it was told — and silver, which reads
    two tables and writes three, produced six edges for three movements. Half
    the graph described data that never moved, and it looked exactly as
    plausible as the half that did.

    So the notebook's own IO helpers record each movement as it happens, and
    the engine attributes the delta to the cell that produced it. A declared
    set drifts from the code the moment either changes; this one cannot,
    because it is the code.
    """
    nb = (ROOT / "platform" / "silver_notebook.py").read_text()
    assert 'LINEAGE.append(("read"' in nb, "the notebook does not record reads"
    assert 'LINEAGE.append(("write"' in nb, "the notebook does not record writes"

    eng = (ROOT / "platform" / "engine.py").read_text()
    assert "recorded[seen:]" in eng, (
        "the engine must attribute the movements of EACH cell, not one set for "
        "the whole notebook — that is what produced the phantom edges"
    )

    src = (ROOT / "platform" / "silver.py").read_text()
    for declared in ("READS", "WRITES"):
        assert f"{declared} = [" not in src, (
            f"silver declares {declared} again — the read/write set must be "
            f"observed by the notebook, never named by the step publishing it"
        )


def test_the_platform_proves_it_runs_unattended():
    """A schedule that is created but never observed to fire proves nothing.

    This is the failure mode the step exists for: if scheduling breaks, the
    data is simply yesterday's and every row count, table and dashboard still
    looks correct. So the assertion is not "a schedule exists" but "a run
    appeared that the SCHEDULER produced" — which only `invokeType` can say,
    because a scheduled run and a manual one are otherwise the same job doing
    the same work.
    """
    src = (ROOT / "platform" / "schedule.py").read_text()
    assert '"Scheduled"' in src, (
        "the step must filter job instances by invokeType — without it the "
        "assertion cannot tell a scheduled run from the manual one that "
        "already ran earlier in the pipeline"
    )
    assert "T.clock_is_controllable" in src, (
        "moving time is emulator-only and must be gated by the target"
    )
    # The real branch must SAY it is not asserting the firing. A silent skip
    # would report success for a scheduler nobody exercised.
    assert "is not asserted here" in src, (
        "the real target skips the firing assertion and must say so out loud"
    )


def test_the_schedule_step_runs_last():
    """The clock jump must not land under another step.

    Proving a schedule fires means advancing the emulator's clock, and every
    job status and long-running operation in the stack derives from that clock.
    An hour jumped mid-run would surface as a completed job or an expired
    operation in whatever step came next — an unrelated-looking failure with a
    cause nobody would find from the error.
    """
    steps = re.findall(
        r'^\s*\("([a-z_]+)",', (ROOT / "platform" / "pipeline.py").read_text(), re.M
    )
    assert steps, "pipeline.py declares no steps"
    assert "schedule" in steps, "the schedule step is not in the pipeline at all"
    after = steps[steps.index("schedule") + 1 :]
    assert not after, f"schedule must run last; these follow it: {after}"


def test_the_clock_advance_fits_inside_one_token_lifetime():
    """Moving Fabric's clock does not move the identity provider's.

    Only the Fabric emulator's clock responds to the advance; the Entra
    emulator that mints the tokens keeps its own. Jump further than a token
    lives and the two disagree permanently: every call afterwards 401s with
    `invalid token: expired`, INCLUDING freshly minted ones, because the new
    token is already past its expiry as far as Fabric is concerned.

    It cost an hour to diagnose once, because it presents as an authentication
    fault and is really a consequence of the clock lever the schedule step
    exists to pull. The step asserts it at import; this asserts the assertion,
    so raising the cadence cannot quietly remove the guard.
    """
    src = (ROOT / "platform" / "schedule.py").read_text()
    m_int = re.search(r"^INTERVAL_MINUTES = (\d+)", src, re.M)
    m_life = re.search(r"^TOKEN_LIFETIME_SECONDS = (\d+)", src, re.M)
    assert m_int and m_life, "the step no longer declares its cadence and lifetime"
    interval, lifetime = int(m_int.group(1)), int(m_life.group(1))
    advance = interval * 60 + 300
    assert advance < lifetime, (
        f"advancing {advance}s outruns the {lifetime}s token lifetime; "
        f"every call after the jump would fail as an auth error"
    )
    assert "TOKEN_LIFETIME_SECONDS" in src and "assert ADVANCE_SECONDS <" in src, (
        "the step must guard this itself, or a future cadence change fails "
        "three calls later with an error naming neither the clock nor this rule"
    )


def test_the_schedule_step_puts_the_clock_back():
    """A stack left an interval ahead hands out tokens Fabric reads as expired.

    The next unrelated command — the portal, a re-run, capture — then fails
    with an authentication error nobody would trace back to a schedule
    assertion minutes earlier. Restoring the offset is part of the step, not
    cleanup someone is trusted to remember.
    """
    src = (ROOT / "platform" / "schedule.py").read_text()
    assert "def reset_clock()" in src, "the step never restores the clock"
    # Before the assert, so a FAILING run also leaves time where it found it.
    body = src[src.index("def main()") :]
    assert body.index("reset_clock()") < body.index("assert len(after) > before"), (
        "the clock must be restored before the assertion, or a failed run "
        "leaves the stack broken for whatever runs next"
    )


def test_the_platform_proves_it_reacts_to_a_delivery():
    """A schedule answers "run at 02:00"; it cannot answer "run when it lands".

    For an external feed that is the question that matters — the vendor's
    export finishes when it finishes, and a fixed hour either reprocesses
    yesterday or processes nothing. So the assertion is that a DROPPED FILE
    started a run, evidenced by `invokeType: "EventTriggered"`, not that a
    trigger record exists.
    """
    src = (ROOT / "platform" / "trigger.py").read_text()
    assert '"EventTriggered"' in src, (
        "the step must filter job instances by invokeType — nothing else can "
        "say the trigger is what started the run"
    )
    assert "T.event_triggers_have_rest_api" in src, (
        "binding a trigger is emulator-only and must be gated by the target"
    )
    # The real branch must SAY it asserts nothing, for the same reason the
    # schedule step must: a silent skip reports success for an unwired feature.
    assert "asserts nothing" in src, (
        "the real target cannot bind a trigger and must say so out loud"
    )


def test_the_trigger_watches_a_marker_not_the_landing_zone():
    """A prefix over the vendor's parts would fire once per part.

    The POS export lands as 21 files. A trigger watching that prefix would
    start 21 refreshes of the same delivery — each one correct in isolation and
    the set of them nonsense. File-arrived and delivery-finished are different
    events, and only the second is worth acting on, which is why the watched
    prefix is a dedicated marker path.
    """
    src = (ROOT / "platform" / "trigger.py").read_text()
    watched = re.search(r'^WATCHED_PREFIX = "([^"]+)"', src, re.M)
    marker = re.search(r'^MARKER = "([^"]+)"', src, re.M)
    assert watched and marker, "the step no longer declares what it watches"
    assert marker.group(1).startswith(watched.group(1)), (
        "the marker must land under the watched prefix, or the trigger cannot fire"
    )
    # The guard that matters: the watched prefix must not be an ancestor of the
    # feed's own parts, which land directly under the vendor directory.
    assert not watched.group(1).rstrip("/").endswith("contoso_pos"), (
        "watching the vendor directory itself fires once per landed part"
    )


def test_the_source_systems_are_named_in_lineage():
    """A medallion does not begin in Fabric. It begins at a vendor.

    Every edge used to need a (workspace, item, path) triple at both ends, so
    the first hop could only be drawn from a file already sitting in
    `Files/landing/` and the system that PUT it there could not be said at all.
    `ingest_pos.py` claimed in its own docstring that the vendor was "a node in
    the lineage graph rather than a filename in Files/landing" long before
    anything made that true.

    A CONNECTION and not a URI: it holds the credential, carries a display
    name, and is what the ingesting client actually authenticated through — so
    naming it records what happened instead of a string this platform invented.
    """
    for step, vendor in (
        ("ingest_pos.py", "Contoso POS"),
        ("ingest_erp_cdc.py", "Contoso ERP"),
    ):
        src = (ROOT / "platform" / step).read_text()
        assert "connections.ensure(" in src, f"{step} names no source system"
        assert vendor in src, f"{step} does not identify {vendor}"
        assert "connections.announce(" in src, f"{step} never reports its lineage"

    helper = (ROOT / "platform" / "connections.py").read_text()
    assert '"connectionId"' in helper, (
        "the read side of a source edge must carry connectionId — a source "
        "system has no workspace and no path inside it"
    )


def test_lineage_reports_use_the_precise_move_form():
    """Flat read/write lists cross-product, and the cross product overstates.

    A step reading two feeds and writing two paths would claim four movements
    where two happened. That is not hypothetical: it put three phantom edges
    into this repository's own graph once, each as plausible-looking as the
    real ones. `moves` pairs each derivation explicitly.
    """
    helper = (ROOT / "platform" / "connections.py").read_text()
    assert '"moves": moves' in helper, (
        "reports must use the precise `moves` form, never flat reads/writes"
    )
    # One move PER path, not one move listing them all: the comprehension is
    # what keeps the customers feed from being credited with the orders file.
    body = helper[helper.index("def from_source") : helper.index("def announce")]
    assert "for p in paths" in body, (
        "from_source must produce one move per landed path — the customers "
        "feed did not produce the orders file"
    )


def test_the_landing_hop_is_reported_so_sources_are_not_orphans():
    """Naming the vendor is not enough on its own.

    bronze reads `abfs://` with Spark, so the emulator sees bytes leave OneLake
    and bytes arrive with nothing tying one to the other — it records no
    landing->bronze edge. Without bronze reporting that hop, the vendor nodes
    hang off landing paths no later edge mentions, and the source systems float
    beside the medallion instead of feeding it.

    The paths must also AGREE. The ingest steps write under a date partition
    and bronze reads the same partition; a reported target that merely looks
    right joins the vendor to a node nothing else references.
    """
    bronze = (ROOT / "platform" / "bronze.py").read_text()
    assert "connections.announce(" in bronze, (
        "bronze must report landing->bronze, or the source nodes are orphans"
    )
    ingest = (ROOT / "platform" / "ingest_pos.py").read_text()
    # Both sides key the path on the landing day, which is what makes the
    # vendor edge and the bronze edge meet at the same node.
    assert "contoso_pos/{day}/" in ingest, (
        "the reported landing path must carry the date partition bronze reads"
    )
    assert "contoso_pos/{day}/" in bronze, "bronze no longer reads a dated partition"


def test_the_partition_columns_match_the_gold_export():
    """A partition must name columns the warehouse really has.

    `export_gold.py` selects snake_case from gold and renames to PascalCase for
    the model; `semantic_model.gold_column` reverses that so a partition's SQL
    names real columns. Two encodings of one mapping is exactly the shape that
    drifts — and this drift is SILENT, because a partition naming a column that
    does not exist fails only when something refreshes the model, which nothing
    in this platform does today. Power BI Desktop would be the first to notice,
    on somebody else's machine.

    So the derivation is checked against the SELECTs the exporter really issues,
    parsed out of its source rather than restated here.
    """
    export = ROOT / "gold" / "tools" / "export_gold.py"
    if not export.exists():
        candidates = list(ROOT.rglob("export_gold.py"))
        assert candidates, "export_gold.py not found — the mapping has no authority"
        export = candidates[0]
    src = export.read_text()

    # Only the pure function is wanted; importing the module would drag in the
    # whole platform (state, fabric, a live target). Compile just the helper.
    text = (ROOT / "platform" / "semantic_model.py").read_text()
    start = text.index("def gold_column")
    end = text.index("def partition")
    ns: dict = {}
    exec(compile(text[start:end], "semantic_model.py", "exec"), ns)
    gold_column = ns["gold_column"]

    # Every `SELECT a, b, c FROM <table>` the exporter issues.
    selects = re.findall(r'"SELECT ([^"]+?) "?\s*"?FROM (\w+)"', src)
    assert selects, f"no SELECTs parsed out of {export.name}"

    for cols, table in selects:
        warehouse_cols = [c.strip() for c in cols.split(",") if c.strip()]
        for wc in warehouse_cols:
            # Round-trip: PascalCase the warehouse column the way the exporter
            # does, then derive it back and require the original.
            model_col = "".join(p.capitalize() for p in wc.split("_"))
            assert gold_column(model_col) == wc, (
                f"{table}.{wc} -> model {model_col} -> {gold_column(model_col)}; "
                f"the partition would SELECT a column that does not exist"
            )
