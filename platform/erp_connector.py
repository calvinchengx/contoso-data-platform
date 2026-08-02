"""Register the Debezium connector, before any DML is replayed.

Order matters and is not incidental. The connector must be RUNNING before
`erp_source.py` applies its history, or those changes happen behind the
connector's start point and are captured — if at all — by a snapshot rather
than as a stream. That would still produce rows, and the counts might even
match, while quietly testing something other than change data capture.
"""

from __future__ import annotations

import json
import pathlib
import time
from typing import LiteralString, cast

import psycopg
import requests
from emulator import DEBEZIUM, ERP_DSN, log

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "sources" / "contoso-erp" / "debezium-connector.json"
SCHEMA = ROOT / "sources" / "contoso-erp" / "schema.sql"


def main() -> int:
    # The table must exist first: `table.include.list` matches nothing against
    # an absent table, and the connector then starts happily and captures
    # nothing at all.
    with psycopg.connect(ERP_DSN, autocommit=True) as conn:
        conn.execute(cast("LiteralString", SCHEMA.read_text()))

    cfg = json.loads(CONFIG.read_text())
    name = cfg["name"]
    r = requests.put(
        f"{DEBEZIUM}/connectors/{name}/config", json=cfg["config"], timeout=60
    )
    assert r.status_code in (200, 201), (r.status_code, r.text[:300])

    # RUNNING is not enough on its own — a connector reports RUNNING while its
    # task has already failed, so both are checked.
    for _ in range(45):
        s = requests.get(f"{DEBEZIUM}/connectors/{name}/status", timeout=30).json()
        conn_state = s.get("connector", {}).get("state")
        tasks = [t.get("state") for t in s.get("tasks", [])]
        if conn_state == "RUNNING" and tasks and all(t == "RUNNING" for t in tasks):
            log(f"Debezium connector {name}: {conn_state}, tasks {tasks}")
            return 0
        if "FAILED" in (conn_state, *tasks):
            raise SystemExit(f"connector failed: {json.dumps(s)[:600]}")
        time.sleep(2)
    raise SystemExit(f"connector never reached RUNNING: {json.dumps(s)[:600]}")


if __name__ == "__main__":
    raise SystemExit(main())
