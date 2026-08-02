"""Invariants this repository must hold on Windows, macOS and Linux.

None of these need the emulator, Docker, or the fixture wheels — they are about
the repository itself, so they are the part of CI that is green from day one and
runs identically on all three platforms.
"""

import pathlib
import re

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

    The family ships on independent cadences — fabric-emulator at 0.13.x while
    entra and keyvault are at 0.3.x — so one pin cannot describe the stack.
    Assuming it could is how this repo first failed to start: `manifest
    unknown`, because 0.13.0 exists for one image and not the others.
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
