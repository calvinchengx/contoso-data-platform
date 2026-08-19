"""Remove build and run artifacts. Portable — shutil, not `rm -rf`."""

import pathlib
import shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent
removed = []
for pat in (
    "**/__pycache__",
    "**/*.egg-info",
    ".venv",
    "capture/shots",
    # dbt's outputs USED to be listed here, when this repository held the gold
    # project. It is the product's now, and so are its artifacts -- a platform
    # that cleaned another repository's working directory would be reaching
    # across a boundary it just spent a release establishing.
    "**/state.json",
    "**/*_summary.json",
):
    for p in ROOT.glob(pat):
        shutil.rmtree(p) if p.is_dir() else p.unlink()
        removed.append(p.relative_to(ROOT).as_posix())
print(f"removed {len(removed)} path(s)" + (":" if removed else ""))
for r in sorted(removed):
    print(f"  {r}")
