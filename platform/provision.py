"""Create the workspace and the lakehouse this platform is built in.

The first code here that talks to the emulator, and the first real test of the
claim this repository makes: everything below is written from the published
quickstart, against a published image, with no access to the emulator's source.
"""

from __future__ import annotations

import state
from fabric import FABRIC_AUD, T, fabric, log, token

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
    # A capacity is required for the workspace to be usable — but WHO provides
    # it differs, and asserting the emulator's convenience would fail against
    # production for a reason unrelated to this code. The emulator seeds one and
    # auto-assigns it; real Fabric expects an existing capacity, assigned by an
    # admin or named in configuration.
    if T.capacity_is_auto_assigned:
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
