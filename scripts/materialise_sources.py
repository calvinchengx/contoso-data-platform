"""Write the vendor exports to disk, so mokapi can serve them.

The bytes come from the SAME seeded generators the fabric-emulator examples
assert against — installed from the pinned release by `make fixtures`. mokapi
serves files rather than generating bodies because its generation is random per
request and random in shape, which cannot back an exact-count assertion.

This step is what makes "the vendor" reproducible: same release, same bytes,
every run, on every platform.
"""

import hashlib
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "sources" / "_data"

# (module, subdirectory).
FEEDS = [
    ("source_system", "contoso-pos"),
    ("web_store", "contoso-web"),
    ("reference_data", "contoso-reference"),
]

# WHY THE PAGES ARE FILES ON DISK, not slices computed per request.
#
# The vendor's API pages, and paging has to actually reduce what a request
# costs or it is decoration. mokapi's `read()` returns a whole file — there is
# no seek, no range — so a handler that read the full export and returned one
# slice of it would spend exactly the memory it spends today. Measured: a
# 95 MB body costs 944 MB, 10.4x, and seven of those killed the container.
#
# Splitting at materialisation makes the page the unit of I/O, so the 10x
# applies to a page instead of the whole export. This is also what a nightly
# batch export usually looks like in practice: parts, not one enormous file.
PAGE_BYTES = 8 * 1024 * 1024


def paginate_json_array(blob: bytes, page_bytes: int = PAGE_BYTES) -> list[bytes]:
    """Split a JSON array into pages that are each a valid JSON array.

    The line-based splitter below cannot touch these: the web store sends
    `[{...},{...}]` on ONE line, so `splitlines()` returns a single 30 MB page
    and paging becomes decoration. That is the shape the page-size comment
    above is about — a 95 MB body cost 944 MB resident and killed the
    container, and 30 MB would carry the same 10x.

    Splitting STRUCTURALLY rather than on bytes is also what the vendor would
    do: a paged REST endpoint returns a page of records as a JSON array, not a
    fragment of one. Each page here is independently parseable, which is what
    lets Spark read the landed directory as one dataset.

    Parsing the whole export is fine HERE and nowhere else — this is a build
    step that runs once, not a request path that runs per page.
    """
    records = json.loads(blob)
    pages, cur, size = [], [], 0
    for rec in records:
        line = json.dumps(rec, separators=(",", ":")).encode()
        cur.append(line)
        size += len(line) + 1
        if size >= page_bytes:
            pages.append(b"[" + b",".join(cur) + b"]")
            cur, size = [], 0
    if cur or not pages:
        pages.append(b"[" + b",".join(cur) + b"]")
    return pages


def paginate(
    blob: bytes, keep_header: bool, page_bytes: int = PAGE_BYTES
) -> list[bytes]:
    """Split on line boundaries into pages of roughly `page_bytes`.

    Line boundaries, never a fixed byte offset: a page that splits mid-record
    is not a page of anything. `keep_header` repeats the CSV header in every
    part, so each page is independently readable — which is what lets Spark
    read the landed directory as one dataset.
    """
    lines = blob.splitlines(keepends=True)
    header = lines[0] if keep_header and lines else b""
    body = lines[1:] if keep_header else lines

    pages, cur, size = [], [], 0
    for line in body:
        cur.append(line)
        size += len(line)
        if size >= page_bytes:
            pages.append(header + b"".join(cur))
            cur, size = [], 0
    if cur or not pages:
        pages.append(header + b"".join(cur))
    return pages


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
        # The vendor's own credential, written where the vendor (mokapi) can
        # read it. NOT copied into serve.js: two literals of one credential
        # drift the moment either moves — which is exactly what happened when
        # this was invented in two places and only one of them was updated.
        (dest / ".api-key").write_text(mod.API_KEY, encoding="utf-8")
        for filename, blob in mod.export(mod.API_KEY).items():
            stem, _, ext = filename.rpartition(".")

            # PARQUET IS NOT PAGEABLE, and the line splitter below would not
            # refuse it — it would return the file whole today (joining split
            # lines reconstructs the bytes exactly) and start corrupting it the
            # day the export crosses PAGE_BYTES. A binary format with a footer
            # has no line boundaries to split on; half a Parquet file is not a
            # smaller Parquet file, it is nothing. So it is served whole, and
            # the size is asserted rather than assumed.
            #
            # The checksum is written beside it because this is the one format
            # where corruption is quiet: Parquet keeps its `PAR1` magic and
            # footer through byte-level damage, so it passes every cheap check
            # while being unreadable. serve.js publishes this digest and
            # ingest_reference verifies it, which turns a silent mangling into
            # one assertion at the vendor boundary.
            if ext == "parquet":
                assert len(blob) <= PAGE_BYTES, (
                    f"{subdir}/{filename} is {len(blob):,} bytes, past the "
                    f"{PAGE_BYTES:,} this vendor serves whole — Parquet cannot "
                    f"be paged, so this needs a real answer, not a bigger number"
                )
                (dest / filename).write_bytes(blob)
                digest = hashlib.sha256(blob).hexdigest()
                (dest / f"{stem}.sha256").write_text(digest, encoding="utf-8")
                total += len(blob)
                print(
                    f"  {subdir}/{filename:26} {len(blob):>12,} bytes  "
                    f"sha256:{digest[:12]}  whole"
                )
                continue

            pagedir = dest / stem
            # Rebuilt, not merged: a stale page from a previous page size would
            # be served as though it belonged to this export.
            if pagedir.exists():
                shutil.rmtree(pagedir)
            pagedir.mkdir(parents=True)

            # JSON arrays page by record, everything else by line. The
            # extension is the vendor's own statement about its format, so it
            # is what decides — not a per-feed flag this script would have to
            # keep in step with the generators.
            if ext == "json":
                pages = paginate_json_array(blob)
            else:
                pages = paginate(blob, keep_header=ext == "csv")
            for i, page in enumerate(pages, 1):
                (pagedir / f"page-{i:04d}.{ext}").write_bytes(page)
            # The page count is DATA, not a constant in two places. serve.js
            # reads it to answer X-Total-Pages, so the API cannot claim a page
            # count the directory does not have.
            (pagedir / "pages.txt").write_text(str(len(pages)), encoding="utf-8")

            total += len(blob)
            digest = hashlib.sha256(blob).hexdigest()[:12]
            print(
                f"  {subdir}/{filename:20} {len(blob):>12,} bytes  "
                f"sha256:{digest}  {len(pages)} page(s)"
            )
    print(f"materialised {total:,} bytes into {OUT.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
