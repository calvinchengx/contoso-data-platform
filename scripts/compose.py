"""Run docker compose with the pinned release in the environment.

The pin lives in .emulator-version. Reading it from the Makefile would need
$(shell cat ...) — which is not a thing on cmd.exe, where GNU Make on Windows
runs its recipes. So the Makefile stays a one-liner and the logic lives here,
where `pathlib.read_text()` means the same on all three platforms.
"""
import os
import pathlib
import subprocess
import sys

import release_info as rel

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = ["compose/docker-compose.yml", "compose/sources.yml"]


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: compose.py <up|down|config|ps> [args...]")
    args = sys.argv[1:]
    env = {**os.environ, "FABRIC_EMULATOR_VERSION": rel.version()}
    cmd = ["docker", "compose"]
    for f in FILES:
        cmd += ["-f", f]
    cmd += args
    print("$", " ".join(cmd), f"   (FABRIC_EMULATOR_VERSION={rel.version()})")
    return subprocess.run(cmd, cwd=ROOT, env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
