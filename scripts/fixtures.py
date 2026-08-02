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


def main():
    v = rel.version()
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
            f"to a release that has them.")

    # Both together, always. contoso-fixtures-advanced requires contoso-fixtures
    # as plain metadata — its [tool.uv.sources] path is a uv-local convenience
    # that does not survive into the wheel — so resolving it from an index would
    # fail. Installing the pair is what makes it resolvable.
    subprocess.run(["uv", "pip", "install", *urls], cwd=ROOT, check=True)

    # Lockstep, asserted rather than assumed: verifying image X with generators
    # from Y would produce confident, wrong numbers — the worst failure
    # available to this repository.
    out = subprocess.run(
        ["uv", "run", "--no-sync", "python", "-c",
         "import importlib.metadata as m; print(m.version('contoso-fixtures'))"],
        cwd=ROOT, capture_output=True, text=True, check=True)
    got = out.stdout.strip()
    if got != v:
        sys.exit(f"installed contoso-fixtures {got}, but .emulator-version "
                 f"pins {v} — these must match")
    print(f"fixtures {got} installed and matched to the pinned release")


if __name__ == "__main__":
    sys.exit(main() or 0)
