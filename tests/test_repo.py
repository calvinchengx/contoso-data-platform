"""Invariants this repository must hold on Windows, macOS and Linux.

None of these need the emulator, Docker, or the fixture wheels — they are about
the repository itself, so they are the part of CI that is green from day one and
runs identically on all three platforms.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAKEFILE = (ROOT / "Makefile").read_text()


def test_pinned_version_is_a_release():
    v = (ROOT / ".emulator-version").read_text().strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", v), v


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
    banned = re.compile(r"(\|\||&&|\|(?!\|)|`|\brm\b|\bcp\b|\bmv\b|\bcat\b|"
                        r"\bsed\b|\btest\b\s+-|\bif\b\s|\bfor\b\s|\$\(shell)")
    offenders = []
    for line in MAKEFILE.splitlines():
        if not line.startswith("\t"):
            continue
        recipe = line.lstrip("\t").lstrip("@")
        if banned.search(recipe):
            offenders.append(recipe)
    assert not offenders, ("these recipes would not run on cmd.exe: "
                           f"{offenders}")


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
    files = [ROOT / "Makefile"] + sorted((ROOT / ".github/workflows").glob("*.yml"))
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
