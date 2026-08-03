"""Publish gold as a semantic model and query it the way Power BI does.

This is the readiness check for a BI client: a SemanticModel item with a TMSL
definition and measures, queried with **DAX over the Power BI `executeQueries`
wire** — the same REST surface Power BI Desktop, the service, and SemPy use.

IMPORT, NOT DIRECT LAKE, and the difference is stated rather than hidden. A
production Fabric model would bind Direct Lake to gold and read it in place. The
rows here are exported over TDS and embedded in the definition — a real Fabric
pattern, and the one this stack serves today.

The audience matters and is asserted: `executeQueries` takes a Power BI token,
not the control-plane one. A surface that accepted either would be teaching the
wrong thing about Fabric's auth model.
"""

from __future__ import annotations

import base64
import json
import pathlib
import time

import state
from fabric import FABRIC, FABRIC_AUD, S, ensure_audience, log, token

from gold import in_dbt_container

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXPORT = ROOT / "gold" / "_export.json"

PBI_AUD = "https://analysis.windows.net/powerbi/api"
MODEL = "ContosoRevenue"

# One query, asked over two surfaces. xmla_probe.py runs this same DAX through
# ADOMD.NET, so if both answer they must agree — which is a stronger statement
# than either surface makes alone.
DAX = (
    "EVALUATE SUMMARIZECOLUMNS(Customer[Country], "
    '"Revenue", [Total Revenue], "PerUnit", [Revenue per Unit])'
)


def definition(rows: dict) -> dict:
    """TMSL plus the rows, as InlineBase64 definition parts."""
    model = {
        "name": MODEL,
        "compatibilityLevel": 1550,
        "model": {
            "culture": "en-US",
            "tables": [
                {
                    "name": "Customer",
                    "columns": [
                        {"name": c, "dataType": "string", "sourceColumn": c}
                        for c in ("CustomerId", "Name", "Country")
                    ],
                },
                {
                    "name": "Revenue",
                    "columns": [
                        {
                            "name": "OrderDate",
                            "dataType": "string",
                            "sourceColumn": "OrderDate",
                        },
                        {
                            "name": "Country",
                            "dataType": "string",
                            "sourceColumn": "Country",
                        },
                        {
                            "name": "Orders",
                            "dataType": "int64",
                            "sourceColumn": "Orders",
                        },
                        {"name": "Units", "dataType": "int64", "sourceColumn": "Units"},
                        {
                            "name": "Revenue",
                            "dataType": "double",
                            "sourceColumn": "Revenue",
                        },
                    ],
                    # Measures are the point of a semantic model: a column plus
                    # how you aggregate it, which is the thing a table cannot
                    # say and a report writer needs.
                    "measures": [
                        {
                            "name": "Total Revenue",
                            "expression": "SUM(Revenue[Revenue])",
                        },
                        {"name": "Total Units", "expression": "SUM(Revenue[Units])"},
                        {
                            "name": "Revenue per Unit",
                            "expression": "DIVIDE([Total Revenue], [Total Units])",
                        },
                    ],
                },
            ],
            "relationships": [
                {
                    "name": "Revenue_Customer",
                    "fromTable": "Revenue",
                    "fromColumn": "Country",
                    "toTable": "Customer",
                    "toColumn": "Country",
                }
            ],
        },
    }

    def part(path: str, obj: dict) -> dict:
        return {
            "path": path,
            "payloadType": "InlineBase64",
            "payload": base64.b64encode(json.dumps(obj).encode()).decode(),
        }

    return {"parts": [part("model.bim", model), part("data.json", rows)]}


def publish(workspace: str, defn: dict, tok: str) -> str:
    h = {"Authorization": f"Bearer {tok}"}
    r = S.post(
        f"{FABRIC}/v1/workspaces/{workspace}/items",
        headers=h,
        json={"displayName": MODEL, "type": "SemanticModel", "definition": defn},
        timeout=120,
    )
    assert r.status_code in (201, 202), (r.status_code, r.text[:300])
    if r.status_code == 201:
        return r.json()["id"]

    # 202 is the long-running-operation path, which real Fabric uses for most
    # mutations. Poll it rather than assuming the sync shape.
    op = r.headers["x-ms-operation-id"]
    for _ in range(60):
        status = S.get(f"{FABRIC}/v1/operations/{op}", headers=h, timeout=30).json()[
            "status"
        ]
        if status in ("Succeeded", "Failed"):
            break
        time.sleep(1)
    assert status == "Succeeded", f"publish operation {status}"
    return S.get(f"{FABRIC}/v1/operations/{op}/result", headers=h, timeout=30).json()[
        "id"
    ]


def main() -> int:
    import source_system as src

    st = state.load()

    log("exporting gold from the warehouse over TDS")
    rc = in_dbt_container("--entrypoint", "python", "dbt", "/tools/export_gold.py")
    assert rc == 0, f"gold export failed: exit {rc}"
    rows = json.loads(EXPORT.read_text())
    assert rows["Revenue"] and rows["Customer"], "the export is empty"

    dataset = publish(st["workspace"], definition(rows), token(FABRIC_AUD))
    state.save(dataset=dataset)

    # Queried exactly as a Power BI REST client would.
    ensure_audience(PBI_AUD, "Power BI Service")
    dax = (
        "EVALUATE SUMMARIZECOLUMNS(Customer[Country], "
        '"Revenue", [Total Revenue], "PerUnit", [Revenue per Unit])'
    )
    url = (
        f"{FABRIC}/v1.0/myorg/groups/{st['workspace']}"
        f"/datasets/{dataset}/executeQueries"
    )
    r = S.post(
        url,
        headers={"Authorization": f"Bearer {token(PBI_AUD)}"},
        json={"queries": [{"query": dax}]},
        timeout=120,
    )
    assert r.status_code == 200, (r.status_code, r.text[:300])
    result = r.json()["results"][0]["tables"][0]["rows"]
    assert result, r.text[:300]

    # The measure has to agree with the fixture, not merely return something.
    total = sum(row["[Revenue]"] for row in result)
    assert abs(total - src.EXPECTED_REVENUE) < 0.01, (total, src.EXPECTED_REVENUE)
    countries = {row["Customer[Country]"] for row in result}
    assert countries == src.EXPECTED_COUNTRIES, (
        sorted(countries),
        src.EXPECTED_COUNTRIES,
    )

    # A control-plane token must be refused. Fabric's audiences are not
    # interchangeable, and a surface that accepted either would be teaching the
    # wrong thing about its auth model.
    r = S.post(
        url,
        headers={"Authorization": f"Bearer {token(FABRIC_AUD)}"},
        json={"queries": [{"query": dax}]},
        timeout=60,
    )
    assert r.status_code == 401, (
        f"a non-Power BI audience was accepted: {r.status_code}"
    )

    log(
        f"semantic model {dataset}: DAX over executeQueries — "
        f"{total:,.2f} revenue across {sorted(countries)}"
    )
    log("executeQueries rejects a non-Power BI audience token (401)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
