"""Re-pin one release back, so a failure can be attributed.

Used only by the Attribute workflow. If the identical suite passes on N-1 and
fails on N, the release regressed; if it fails on both, the fault is here.
"""
import json
import pathlib
import sys
import urllib.request

import release_info as rel

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = f"https://api.github.com/repos/{rel.REPO}/releases?per_page=20"


def main():
    current = rel.version()
    with urllib.request.urlopen(API, timeout=30) as r:
        releases = json.load(r)
    tags = [x["tag_name"].lstrip("v") for x in releases if not x.get("prerelease")]
    if current not in tags:
        sys.exit(f"pinned {current} is not among the latest releases: {tags[:5]}")
    i = tags.index(current)
    if i + 1 >= len(tags):
        sys.exit(f"{current} is the oldest release listed — nothing to compare against")
    previous = tags[i + 1]
    (ROOT / ".emulator-version").write_text(previous + "\n")
    print(f"re-pinned {current} -> {previous} for attribution")
    return 0


if __name__ == "__main__":
    sys.exit(main())
