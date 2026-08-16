"""Resolve the workspace and lakehouse by NAME, creating them if absent.

BY NAME, NOT BY ID, and that is the documented contract rather than a
convenience: ids can never match across targets, so user code holds display
names and the platform resolves them to a GUID per target
(docs/21-real-fabric-toggle).

Resolve-or-create also makes the run idempotent. Display names are unique per
tenant in real Fabric, so a second `POST /workspaces` returns 409
`WorkspaceNameAlreadyExists` — on both targets. A platform that can only be run
against a fresh tenant is not one anybody can operate.
"""

from __future__ import annotations

import capacity
import state
from fabric import FABRIC_AUD, fabric, log, token

# From the target, not restated here: real mode is workspace-scoped and the
# resolver needs the name before this module is even imported. One string, one
# place — a second copy would be a second answer the day it drifts.
from target import WORKSPACE

LAKEHOUSE = "contoso_lake"


def find_workspace(tok: str, name: str) -> dict | None:
    r = fabric("GET", "/workspaces", tok)
    assert r.status_code == 200, (r.status_code, r.text[:200])
    for ws in r.json().get("value", []):
        if ws.get("displayName") == name:
            return ws
    return None


def find_item(tok: str, workspace: str, name: str, kind: str) -> dict | None:
    r = fabric("GET", f"/workspaces/{workspace}/items", tok)
    # A stale state.json is the common cause, not a broken workspace: the
    # emulator keeps state in memory, so any restart invalidates the ids a
    # previous run recorded. Say that, rather than leaving `WorkspaceNotFound`
    # to be interpreted.
    assert r.status_code != 404, (
        f"workspace {workspace} is gone — state.json is from an earlier stack. "
        f"Run `make verify` from the start; provision resolves by name."
    )
    assert r.status_code == 200, (r.status_code, r.text[:200])
    for it in r.json().get("value", []):
        if it.get("displayName") == name and it.get("type") == kind:
            return it
    return None


def main() -> int:
    tok = token(FABRIC_AUD)

    # The capacity first, because the workspace has to be assigned to one and
    # the control plane takes a moment to learn about a freshly created
    # capacity. Doing it here overlaps that wait with the workspace call below.
    cap = capacity.resolve(tok)

    ws = find_workspace(tok, WORKSPACE)
    if ws is None:
        r = fabric("POST", "/workspaces", tok, json={"displayName": WORKSPACE})
        # Create is one of the synchronous paths (quickstart §3). A 202 would
        # mean the contract changed, which is worth failing on rather than
        # silently polling.
        assert r.status_code == 201, (r.status_code, r.text[:300])
        ws = r.json()
        log(f"created workspace {WORKSPACE}")
    else:
        log(f"reusing workspace {WORKSPACE}")
    assert ws["id"], ws

    # Put the workspace on the capacity we resolved. This is one call on both
    # targets and asserts the same thing on both, which it could not do while
    # the emulator was seeding a capacity of its own and this platform was
    # merely noticing. Skipped when it is already there, so a re-run is quiet.
    if ws.get("capacityId") != cap:
        r = fabric(
            "POST",
            f"/workspaces/{ws['id']}/assignToCapacity",
            tok,
            json={"capacityId": cap},
        )
        assert r.status_code == 202, (r.status_code, r.text[:300])
        log(f"assigned {WORKSPACE} to capacity {cap}")

    # True on BOTH targets now, so it is asserted on both.
    r = fabric("GET", f"/workspaces/{ws['id']}", tok)
    assert r.status_code == 200, (r.status_code, r.text[:200])
    assert r.json().get("capacityId") == cap, (
        f"workspace is on capacity {r.json().get('capacityId')}, expected {cap}"
    )

    lake = find_item(tok, ws["id"], LAKEHOUSE, "Lakehouse")
    if lake is None:
        r = fabric(
            "POST",
            f"/workspaces/{ws['id']}/items",
            tok,
            json={"displayName": LAKEHOUSE, "type": "Lakehouse"},
        )
        assert r.status_code in (201, 202), (r.status_code, r.text[:300])
        lake = r.json()
        log(f"created lakehouse {LAKEHOUSE}")
    else:
        log(f"reusing lakehouse {LAKEHOUSE}")
    assert lake["id"], lake

    state.save(
        workspace=ws["id"],
        lakehouse=lake["id"],
        workspace_name=WORKSPACE,
        lakehouse_name=LAKEHOUSE,
    )
    log(f"workspace {ws['id']}, lakehouse {lake['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
