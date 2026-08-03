"""Install the seeded generators published by the pinned release.

These are the SAME generators the four in-tree medallion examples use. That is
the entire boundary between the two repositories: the data and the expectations
have one source, so a number asserted here and a number asserted there cannot
quietly describe different datasets.

Installed by URL at the pinned tag, so the version cannot be stated twice and
drift — the tag IS the URL.
"""

import os
import pathlib
import subprocess
import sys

import release_info as rel

ROOT = pathlib.Path(__file__).resolve().parent.parent


def local_override():
    """Wheels built from a fabric-emulator checkout, for developing ahead of a
    release. Announced loudly every time: a local artifact quietly standing in
    for the published one would make this repository's whole claim — that a
    RELEASED emulator carries a platform — untrue while still reporting green.
    """
    d = os.environ.get("FIXTURE_WHEELS_DIR")
    if not d:
        return None
    wheels = sorted(pathlib.Path(d).glob("*.whl"))
    if not wheels:
        sys.exit(f"FIXTURE_WHEELS_DIR={d} contains no wheels")
    # Extras apply to local wheels too. Without them fabric-target installs but
    # cannot authenticate, and the lazy import means that surfaces at the first
    # call rather than here — the exact confusion this override exists to avoid.
    specs = []
    for w in wheels:
        name = w.name.split("-")[0]  # `fabric_target-0.14.1-py3-...whl`
        extras = rel.EXTRAS.get(name)
        if extras:
            # A PEP 508 direct reference needs a URI, not a bare path.
            specs.append(f"{name.replace('_', '-')}{extras} @ {w.resolve().as_uri()}")
        else:
            specs.append(str(w))
    print("!" * 72)
    print("LOCAL WHEELS — not the published artifact. This run verifies your")
    print(f"working tree, NOT a released fabric-emulator.  {d}")
    print("!" * 72)
    return specs


def ensure_env():
    """Create the project venv before installing into it.

    `uv pip install` needs a target environment; on a fresh clone there is
    none, and its exit code 2 says nothing about the cause. `uv sync` builds it
    from pyproject.toml and is a no-op afterwards.
    """
    subprocess.run(["uv", "sync", "--quiet"], cwd=ROOT, check=True)


def main():
    v = rel.version()
    ensure_env()
    override = local_override()
    if override:
        subprocess.run(["uv", "pip", "install", *override], cwd=ROOT, check=True)
        print("installed local fixture wheels (version lockstep NOT asserted)")
        return 0

    urls = rel.wheel_urls(v)
    all_there, per = rel.wheels_published(v)

    if all_there is None:
        sys.exit("could not reach github.com to check for the fixture wheels")
    if not all_there:
        missing = "\n  ".join(u for u, ok in per.items() if not ok)
        sys.exit(
            f"fabric-emulator {v} publishes no fixture wheels.\n\n"
            f"  missing:\n  {missing}\n\n"
            f"They are built by scripts/build_fixture_wheels.py in that repo and\n"
            f"attached from the first release carrying it. Bump .emulator-version\n"
            f"to a release that has them."
        )

    # Both together, always. contoso-fixtures-advanced requires contoso-fixtures
    # as plain metadata — its [tool.uv.sources] path is a uv-local convenience
    # that does not survive into the wheel — so resolving it from an index would
    # fail. Installing the pair is what makes it resolvable.
    subprocess.run(["uv", "pip", "install", *urls], cwd=ROOT, check=True)

    # Lockstep, asserted rather than assumed: verifying image X with generators
    # from Y would produce confident, wrong numbers — the worst failure
    # available to this repository.
    out = subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "python",
            "-c",
            "import importlib.metadata as m; print(m.version('contoso-fixtures'))",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    got = out.stdout.strip()
    if got != v:
        sys.exit(
            f"installed contoso-fixtures {got}, but .emulator-version "
            f"pins {v} — these must match"
        )
    print(f"fixtures {got} installed and matched to the pinned release")


if __name__ == "__main__":
    sys.exit(main() or 0)
