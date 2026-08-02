"""Create the workspace and the lakehouse this platform is built in.

The first code here that talks to the emulator, and the first real test of the
claim this repository makes: everything below is written from the published
quickstart, against a published image, with no access to the emulator's source.
"""

from __future__ import annotations

import state
from emulator import FABRIC_AUD, fabric, log, token

WORKSPACE = "contoso-analytics"
LAKEHOUSE = "contoso_lake"


def main() -> int:
    tok = token(FABRIC_AUD)

    r = fabric("POST", "/workspaces", tok, json={"displayName": WORKSPACE})
    # Create is one of the synchronous paths (quickstart §3). A 202 here would
    # mean the emulator changed its contract, which is worth failing on rather
    # than silently polling.
    assert r.status_code == 201, (r.status_code, r.text[:300])
    ws = r.json()
    assert ws["id"], ws
    # The docs promise a capacity is seeded and auto-assigned, so tools that
    # refuse capacity-less workspaces work out of the box. Asserted, because it
    # is a promise a consumer depends on and cannot see until it breaks.
    assert ws.get("capacityId"), f"no capacity auto-assigned: {ws}"

    r = fabric(
        "POST",
        f"/workspaces/{ws['id']}/items",
        tok,
        json={"displayName": LAKEHOUSE, "type": "Lakehouse"},
    )
    assert r.status_code in (201, 202), (r.status_code, r.text[:300])
    lake = r.json()
    assert lake["id"], lake

    state.save(
        workspace=ws["id"],
        lakehouse=lake["id"],
        workspace_name=WORKSPACE,
        lakehouse_name=LAKEHOUSE,
    )
    log(f"provisioned workspace {ws['id']} and lakehouse {lake['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
