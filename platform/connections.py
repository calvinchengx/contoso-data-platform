"""The source systems, as first-class objects — and as lineage nodes.

A medallion does not begin in Fabric. It begins at a vendor's REST API, a
production database, a change stream. But every lineage edge used to require a
(workspace, item, path) triple at both ends, so the first hop could only ever
be drawn from a file already sitting in `Files/landing/` — and the system that
PUT it there could not be said at all. `ingest_pos.py` has claimed since it was
written that the vendor's spec "is what makes Contoso POS a node in the lineage
graph rather than a filename in Files/landing"; until this module existed that
was an aspiration, and the graph started at the landed file.

WHY A CONNECTION AND NOT A URI. The connection already exists in Fabric's model:
it holds the vendor's credential, it carries a display name and a connectivity
type, and it is what the ingesting client actually authenticated through.
Naming it keeps the rule an edge lives by — record what happened, never a guess
about it. A free-form URI would be a string this platform made up.

CREDENTIALS STILL COME FROM KEY VAULT. The connection is created with the
secret read from the vault at call time, exactly as the ingest steps read it to
authenticate. Nothing here puts a credential in the source tree, and the value
is never read back out of Fabric — the read shape is metadata only, as in real
Fabric.

ONE ASYMMETRY. Creating a connection is real Fabric's own API. REPORTING
lineage is not: `POST …/lineage` is an emulator-native extension, because real
Fabric derives lineage from the artifacts it manages rather than accepting a
claim. So the connection is created on both targets and the report is gated —
see `T.lineage_can_be_reported`.
"""

from __future__ import annotations

import json

from fabric import T, fabric, log


def ensure(tok: str, display_name: str, connectivity: str, details: dict) -> str:
    """Resolve-or-create a connection by DISPLAY NAME.

    By name for the same reason every other object here is: ids cannot match
    across targets, and a step that only works against a fresh tenant is not
    one anybody can operate.
    """
    listed = fabric("GET", "/connections", tok)
    assert listed.status_code == 200, (listed.status_code, listed.text[:200])
    for c in listed.json().get("value", []):
        if c.get("displayName") == display_name:
            return c["id"]

    r = fabric(
        "POST",
        "/connections",
        tok,
        json={
            "displayName": display_name,
            "connectivityType": connectivity,
            "connectionDetails": details,
        },
    )
    assert r.status_code in (200, 201), (r.status_code, r.text[:300])
    created = r.json()
    log(f"connection {display_name} ({connectivity})")
    return created["id"]


def report(tok: str, workspace: str, step: str, moves: list[dict]) -> bool:
    """Report what a step moved. Returns whether anything was recorded.

    `moves` is the PRECISE form — a list of `{reads, writes}` derivations —
    rather than one flat read set and one flat write set. The flat form pairs
    every read with every write, which overstates the moment a step has more
    than one of either: a step reading two feeds and writing two paths would
    claim four movements where two happened. That exact mistake produced a
    graph with three phantom edges in this repository once already.
    """
    if not T.lineage_can_be_reported:
        return False
    r = fabric(
        "POST",
        f"/workspaces/{workspace}/lineage",
        tok,
        json={"step": step, "moves": moves},
    )
    assert r.status_code in (200, 201), (r.status_code, r.text[:300])
    return True


def from_source(connection: str, item: str, paths: list[str]) -> list[dict]:
    """Movements from ONE source system into the paths it landed.

    One move per path, never one move listing them all: the customers feed did
    not produce the orders file. The read side carries only `connectionId` —
    a source system has no workspace and no path inside it, and the emulator
    rejects a ref that tries to be both.
    """
    return [
        {
            "reads": [{"connectionId": connection}],
            "writes": [{"itemId": item, "path": p}],
        }
        for p in paths
    ]


def announce(tok: str, workspace: str, step: str, name: str, moves: list[dict]) -> None:
    """Report, and say what was recorded — or why nothing was.

    A step that silently skipped its lineage would leave a graph that begins at
    a landed file and looks perfectly correct, which is the failure this whole
    module exists to remove.
    """
    if report(tok, workspace, step, moves):
        landed = [w["path"] for m in moves for w in m["writes"]]
        log(f"lineage: {name} -> {', '.join(landed)}")
    else:
        log(
            f"lineage: not reported — {T.name} derives lineage from the "
            f"artifacts it manages and accepts no claim ({name})"
        )


def details(**kw) -> dict:
    """connectionDetails as Fabric wants it: a JSON object describing where the
    source lives. Kept a helper so each vendor's shape is written once, beside
    the values it is built from."""
    return json.loads(json.dumps(kw))
