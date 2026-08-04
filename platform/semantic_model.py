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

import pbip
import state
from fabric import FABRIC, FABRIC_AUD, S, T, ensure_audience, fabric, log, token
from provision import find_item

import gold
from gold import in_dbt_container

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXPORT = ROOT / "gold" / "_export.json"

PBI_AUD = "https://analysis.windows.net/powerbi/api"
MODEL = "ContosoRevenue"

# Which gold table each model table projects from. Written here rather than
# derived from the name: `Revenue` comes from `fct_daily_revenue`, and a
# convention that guessed would break the first time a model table was renamed
# for the report writer's benefit — which is the whole point of a semantic layer.
GOLD_TABLE = {
    "Customer": "dim_customer",
    "Revenue": "fct_daily_revenue",
    "Reporting": "fct_revenue_summary",
}

# One query, asked over two surfaces. xmla_probe.py runs this same DAX through
# ADOMD.NET, so if both answer they must agree — which is a stronger statement
# than either surface makes alone.
DAX = (
    "EVALUATE SUMMARIZECOLUMNS(Customer[Country], "
    '"Revenue", [Total Revenue], "PerUnit", [Revenue per Unit])'
)


def sql_endpoint(workspace: str, warehouse: str, tok: str) -> str:
    """Where a SQL client dials to reach the Warehouse, asked of Fabric.

    DISCOVERED, never configured. Real Fabric returns
    `properties.connectionString` on a Warehouse item, and it is per-workspace —
    so an endpoint written into a config file is one that cannot be right on
    two targets at once. Asking the API is the same call on both.
    """
    r = fabric("GET", f"/workspaces/{workspace}/items/{warehouse}", tok)
    assert r.status_code == 200, (r.status_code, r.text[:200])
    cs = (r.json().get("properties") or {}).get("connectionString")
    assert cs, (
        "the Warehouse advertises no connectionString. On the emulator that "
        "means no SQL endpoint is running (FABRIC_SQL_TDS_ADDR unset), and a "
        "model whose partitions name no server loads nothing."
    )
    return cs


def gold_column(model_column: str) -> str:
    """The warehouse column a model column projects from.

    Gold is snake_case because dbt wrote it; the model is PascalCase because a
    report writer reads it. `export_gold.py` already performs this mapping when
    it selects rows, and a SECOND hand-written copy here would drift — silently,
    because a partition naming a column that does not exist fails only when
    something actually refreshes, which today is nothing.

    So it is derived, and `test_the_partition_columns_match_the_gold_export`
    pins the derivation against the SELECTs export_gold.py really issues.
    """
    out = []
    for i, ch in enumerate(model_column):
        if ch.isupper() and i:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def partition(table: str, columns: list[dict], server: str, database: str) -> dict:
    """Where this table's rows come from, as a Fabric import partition.

    WHY A PARTITION AT ALL. A table's columns say what it looks like; its
    partition says where the rows come from. Without one the model is a shape
    with nothing behind it — which is what this platform published until now,
    leaning on `data.json`, an emulator-native convenience the model carried
    instead of a source. Power BI Desktop opens such a model to empty tables,
    and nothing about the definition says why.

    `Value.NativeQuery` with explicit aliases rather than navigating to the
    table: gold is snake_case and the model is PascalCase, so the SELECT is
    where that mapping belongs. Leaving it implicit would make `sourceColumn`
    a claim nobody checks.
    """
    cols = ", ".join(
        f"[{gold_column(c['sourceColumn'])}] AS [{c['name']}]" for c in columns
    )
    query = f"SELECT {cols} FROM [dbo].[{table}]"
    m = (
        "let\n"
        f'    Source = Sql.Database("{server}", "{database}"),\n'
        f'    Data = Value.NativeQuery(Source, "{query}")\n'
        "in\n"
        "    Data"
    )
    return {
        "name": table,
        "mode": "import",
        "source": {"type": "m", "expression": m},
    }


def definition(rows: dict, server: str = "", database: str = "") -> dict:
    """TMSL plus the rows, as InlineBase64 definition parts.

    `rows` and the partitions coexist deliberately. The partition is what a BI
    client follows to refresh; `data.json` is what the emulator's bounded DAX
    evaluator reads to answer a query today. Dropping either would break one of
    the two consumers, and they do not disagree — both come from the same gold
    export.
    """
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
                # --- the management reporting table -----------------------
                # WHAT A P&L PACK IS ACTUALLY BUILT ON, and the table that
                # makes this model answer management accounting rather than
                # only "how did we trade today". Three axes the Revenue table
                # cannot offer at all: Contoso's 1 April financial year, the
                # group data office's product rollup, and customer segment.
                #
                # NO RELATIONSHIP, deliberately, and this is the contrast worth
                # reading. `Revenue` has to reach `Customer` through Country —
                # a three-value column, so the relationship is many-to-many and
                # can carry a country total and nothing finer. An aggregate
                # that carries its own attributes needs no such bridge: every
                # slice below is a column on the row it describes.
                {
                    "name": "Reporting",
                    "columns": [
                        {"name": c, "dataType": t, "sourceColumn": c}
                        for c, t in (
                            ("FiscalYearLabel", "string"),
                            ("FiscalQuarterLabel", "string"),
                            ("Department", "string"),
                            ("ProductSegment", "string"),
                            ("CustomerSegment", "string"),
                            ("Country", "string"),
                            ("Orders", "int64"),
                            ("Units", "int64"),
                            ("RevenueUsd", "double"),
                            ("RevenueAtCarriedRate", "double"),
                        )
                    ],
                    "measures": [
                        # IN USD, converted per order at that day's rate —
                        # which today equals `Revenue`'s figure exactly,
                        # because Contoso POS stamps every order `USD`. The
                        # two are denominated differently even where they
                        # agree, and only this one stays right when the
                        # storefront's currencies arrive.
                        {
                            "name": "Revenue USD",
                            "expression": "SUM(Reporting[RevenueUsd])",
                        },
                        {
                            "name": "Units Sold",
                            "expression": "SUM(Reporting[Units])",
                        },
                        # HOW MUCH OF THE ABOVE RESTS ON AN ASSUMPTION. FX is
                        # published on trading days only, so weekend trading is
                        # converted at the preceding Friday's rate. Surfacing
                        # the share as a measure means a reviewer can ask the
                        # question without leaving the report.
                        {
                            "name": "Revenue at Carried Rate",
                            "expression": "SUM(Reporting[RevenueAtCarriedRate])",
                        },
                        {
                            "name": "Carried Rate Share",
                            "expression": (
                                "DIVIDE([Revenue at Carried Rate], [Revenue USD])"
                            ),
                        },
                    ],
                },
            ],
            "relationships": [
                # LEFT AS IT WAS, and it is the weak one. Country is a
                # three-value column, so this is many-to-many: it can answer a
                # country total and cannot slice revenue by anything finer.
                # Replacing it means regrading the DAX that semantic_model.py
                # and xmla_probe.py both assert against, which is a change of
                # its own rather than a rider on this one. `Reporting` above is
                # the surface that does not need it.
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

    # Attach a partition per table when the endpoint is known. Guarded rather
    # than assumed so `definition(rows)` stays callable without a live
    # warehouse — the governance step and the tests both do that.
    if server and database:
        tables: list[dict] = model["model"]["tables"]
        for tbl in tables:
            name: str = tbl["name"]
            columns: list[dict] = tbl["columns"]
            tbl["partitions"] = [partition(GOLD_TABLE[name], columns, server, database)]

    return {"parts": [part("model.bim", model), part("data.json", rows)]}


def publish(workspace: str, defn: dict, tok: str) -> str:
    h = {"Authorization": f"Bearer {tok}"}

    # Resolve-or-update, not create. Display names are unique per workspace on
    # both targets, so a second create returns 409 ItemDisplayNameAlreadyInUse —
    # and a platform that only runs against a fresh workspace is not one anybody
    # can operate. Updating also refreshes the embedded rows, which is what a
    # rebuild of gold should do to the model that carries it.
    existing = find_item(tok, workspace, MODEL, "SemanticModel")
    if existing:
        r = S.post(
            f"{FABRIC}/v1/workspaces/{workspace}/items/{existing['id']}"
            f"/updateDefinition",
            headers=h,
            json={"definition": defn},
            timeout=120,
        )
        assert r.status_code in (200, 202), (r.status_code, r.text[:300])
        return existing["id"]

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
    rows = json.loads(EXPORT.read_text(encoding="utf-8"))
    assert rows["Revenue"] and rows["Customer"] and rows["Reporting"], (
        "the export is empty"
    )

    tok = token(FABRIC_AUD)
    # The database name is the one thing the Warehouse differs about across
    # targets, so it is resolved by target.py and not decided here.
    server = sql_endpoint(st["workspace"], st["warehouse"], tok)
    database = T.warehouse_database(st["warehouse"], gold.WAREHOUSE)
    log(f"warehouse SQL endpoint {server}, database {database}")

    defn = definition(rows, server, database)
    dataset = publish(st["workspace"], defn, tok)

    # The same definition, on disk, as a Power BI Project. Written from `defn`
    # rather than rebuilt so the folder describes what was actually published —
    # a second construction could drift by a partition and nobody would see it.
    model_bim = json.loads(base64.b64decode(defn["parts"][0]["payload"]))
    folder = pbip.write(ROOT / "artifacts" / "pbip", model_bim)
    log(f"PBIP written to {folder.relative_to(ROOT)}")
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

    # --- the management reporting pack, over the same wire -----------------
    # A PUBLISHED SURFACE NOBODY QUERIES is a surface nobody finds out has
    # broken. The tables above are asserted, so this one is too — and it is
    # asked the way a Power BI report asks it, by fiscal period and product
    # segment, which is the whole reason this table exists.
    pack_dax = (
        "EVALUATE SUMMARIZECOLUMNS(Reporting[FiscalQuarterLabel], "
        'Reporting[ProductSegment], "Revenue", [Revenue USD], '
        '"Carried", [Revenue at Carried Rate])'
    )
    r = S.post(
        url,
        headers={"Authorization": f"Bearer {token(PBI_AUD)}"},
        json={"queries": [{"query": pack_dax}]},
        timeout=120,
    )
    assert r.status_code == 200, (r.status_code, r.text[:300])
    pack = r.json()["results"][0]["tables"][0]["rows"]
    assert pack, r.text[:300]

    # Graded against the EXPORT, which came from the warehouse — so the model
    # agreeing with itself is not what is being checked here.
    usd = sum(row["[Revenue]"] for row in pack)
    expected_usd = sum(x["RevenueUsd"] for x in rows["Reporting"])
    assert abs(usd - expected_usd) < 0.01, (usd, expected_usd)

    # THE FISCAL YEAR REALLY APPLIED. Contoso's year starts 1 April, so July
    # trading must report as Q2 — a model that quietly fell back to the
    # calendar would say Q3 and every total would still be right.
    quarters = {row["Reporting[FiscalQuarterLabel]"] for row in pack}
    assert quarters == {"FY27 Q2"}, (
        f"expected July 2026 to report as FY27 Q2 on a 1 April financial "
        f"year, got {sorted(quarters)}"
    )
    carried = sum(row["[Carried]"] for row in pack)
    assert carried > 0, (
        "no revenue is flagged as converted at a carried-forward rate — the "
        "weekend FX gaps stopped being filled, or stopped existing"
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
        f"{total:,.2f} revenue across {sorted(countries)}; reporting pack "
        f"{usd:,.2f} USD over {sorted(quarters)}, {carried:,.2f} "
        f"({100 * carried / usd:.1f}%) at a carried-forward rate"
    )
    log("executeQueries rejects a non-Power BI audience token (401)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
