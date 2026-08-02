"""Run the platform, in order, stopping at the first failure.

Steps are NAMED, not numbered: the order lives here and nowhere else, so
inserting one does not renumber a directory.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent

STEPS = [
    ("provision", "workspace and lakehouse"),
    ("ingest_pos", "pull Contoso POS over HTTP into Files/landing"),
]


def main() -> int:
    for i, (step, title) in enumerate(STEPS, 1):
        print(f"==> [{i}/{len(STEPS)}] {title}", flush=True)
        rc = subprocess.run([sys.executable, f"{step}.py"], cwd=HERE).returncode
        if rc != 0:
            return rc or 1
    print(f"==> platform complete: {len(STEPS)}/{len(STEPS)} steps passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
