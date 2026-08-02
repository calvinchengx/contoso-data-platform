"""State what was verified, not merely that nothing failed.

"We verified 0.13.1" is a claim someone can check. "The build was green" is
compatible with the run never having happened — which is exactly what a stale
trigger looks like from the outside.
"""
import pathlib
import sys

import release_info as rel

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    v = rel.version()
    print(f"### Verified `fabric-emulator {v}`\n")
    ok, _ = rel.wheels_published(v)
    print(f"- fixture wheels: {'published' if ok else 'NOT published'} for {v}")

    # Each step writes its own summary; absent ones are reported as absent
    # rather than omitted, so a stage that silently did not run is visible.
    for name in ("silver", "star_silver", "gold_star"):
        p = ROOT / f"{name}_summary.json"
        print(f"- `{name}`: {'produced' if p.exists() else 'no summary written'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
