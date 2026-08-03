"""Pull Contoso POS over HTTP and land it verbatim in OneLake.

The difference between this platform and the emulator's own examples starts
here: they call the generator in-process, this fetches from a REST API the
vendor publishes an OpenAPI spec for. That spec is what makes Contoso POS a
node in the lineage graph rather than a filename in `Files/landing/`.

Landed VERBATIM — no parsing, no reshaping. Bronze's job is to be the bytes as
they arrived, so that a question about the source can be answered without going
back to the vendor.
"""

from __future__ import annotations

import datetime as dt

import requests
import state
from fabric import STORAGE_AUD, log, token, upload

from sources import POS_API, POS_API_KEY

# (operation path, landed filename). Named from the OpenAPI spec's operations,
# so a spec change that renames a route fails here rather than landing an empty
# file that only bronze will notice.
FEEDS = [
    ("/api/v1/export/customers", "customers.csv"),
    ("/api/v1/export/orders", "orders.jsonl"),
]


def fetch(path: str, key: str) -> requests.Response:
    return requests.get(f"{POS_API}{path}", headers={"X-Api-Key": key}, timeout=600)


def main() -> int:
    st = state.load()
    tok = token(STORAGE_AUD)
    day = dt.date.today().isoformat()

    # The credential is enforced by the vendor, not by us. The emulator's own
    # example asserts a wrong key raises PermissionError in-process; over HTTP
    # the same guarantee is a 401, which is what a real client would meet.
    refused = fetch(FEEDS[0][0], "wrong-key")
    assert refused.status_code == 401, (
        f"the vendor accepted a bad API key: {refused.status_code}"
    )

    landed = {}
    for path, filename in FEEDS:
        r = fetch(path, POS_API_KEY)
        assert r.status_code == 200, (path, r.status_code, r.text[:200])
        blob = r.content
        assert blob, f"{path} returned an empty body"

        dest = f"Files/landing/contoso_pos/{day}/{filename}"
        written = upload(st["workspace"], st["lakehouse"], dest, blob, tok)
        assert written == len(blob), (written, len(blob))
        landed[filename] = written
        log(f"landed {dest} — {written:,} bytes")

    state.save(landing_day=day, pos_landed=landed)
    total = sum(landed.values())
    log(f"Contoso POS: {len(landed)} file(s), {total:,} bytes over HTTP")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
