"""Write the vendor exports to disk, so mokapi can serve them.

The bytes come from the SAME seeded generators the fabric-emulator examples
assert against — installed from the pinned release by `make fixtures`. mokapi
serves files rather than generating bodies because its generation is random per
request and random in shape, which cannot back an exact-count assertion.

This step is what makes "the vendor" reproducible: same release, same bytes,
every run, on every platform.
"""
import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "sources" / "_data"

# (module, export kwarg, subdirectory). Web/ERP/reference arrive in later waves.
FEEDS = [("source_system", "contoso-pos")]


def main():
    try:
        import source_system  # noqa: F401
    except ImportError:
        sys.exit("the fixture generators are not installed — run `make fixtures`")

    total = 0
    for mod_name, subdir in FEEDS:
        mod = __import__(mod_name)
        dest = OUT / subdir
        dest.mkdir(parents=True, exist_ok=True)
        for filename, blob in mod.export(mod.API_KEY).items():
            p = dest / filename
            p.write_bytes(blob)
            total += len(blob)
            digest = hashlib.sha256(blob).hexdigest()[:12]
            print(f"  {subdir}/{filename:20} {len(blob):>12,} bytes  sha256:{digest}")
    print(f"materialised {total:,} bytes into {OUT.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
