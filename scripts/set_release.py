"""Point this repository at a specific fabric-emulator release.

WHY THIS EXISTS. The acceptance run is triggered by the emulator's release
workflow, and the whole claim being made is "the release that just shipped
carries a working platform". A run that fired on 0.13.1 but verified the 0.13.0
in `versions.env` would be worse than no run at all: it reports success for a
release nobody tested, and reports it in the emulator's own release history.

TWO VERSIONS MOVE, not one. `sail` is built by the same release workflow with
`type=semver,pattern={{version}}`, so it carries the emulator's tag — and it is
the Spark engine, which decides how bronze and silver actually behave. Leaving
it pinned while moving the emulator would verify a new emulator against an old
engine and call that a release test.

Rewrites in place rather than exporting environment variables, because compose
reads `versions.env` via `--env-file` and `release_info` reads the same file.
One file changes, and every reader — Python, compose, the summary — agrees on
what was tested without any of them being told separately.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSIONS = ROOT / "versions.env"

# The keys the emulator's release tags in lockstep. Anything not listed here
# ships on its own cadence and must NOT be moved by a fabric-emulator release.
TRACKS_THE_RELEASE = ("FABRIC_EMULATOR_VERSION", "SAIL_VERSION")

SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")


def set_version(text: str, version: str) -> tuple[str, dict[str, str]]:
    """Return the rewritten file and what each key moved from."""
    moved = {}
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, old = stripped.partition("=")
        key, old = key.strip(), old.strip()
        if key in TRACKS_THE_RELEASE:
            moved[key] = old
            lines[i] = f"{key}={version}\n"
    return "".join(lines), moved


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit("usage: set_release.py <version>   e.g. set_release.py 0.13.1")
    version = sys.argv[1].lstrip("v")

    # A dispatch that arrives with an empty or malformed payload would
    # otherwise write `FABRIC_EMULATOR_VERSION=` and fail four steps later, as
    # an image pull error that names neither this script nor the payload.
    if not SEMVER.match(version):
        sys.exit(f"not a version: {version!r} — expected something like 0.13.1")

    text = VERSIONS.read_text()
    new, moved = set_version(text, version)

    missing = [k for k in TRACKS_THE_RELEASE if k not in moved]
    if missing:
        sys.exit(f"{VERSIONS.name} has no {', '.join(missing)} to set")

    VERSIONS.write_text(new)
    for key, old in moved.items():
        note = "  (unchanged)" if old == version else ""
        print(f"  {key}: {old} -> {version}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
