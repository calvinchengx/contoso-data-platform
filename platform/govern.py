"""Catalog the platform in OpenMetadata — including the SOURCE SYSTEMS.

THIS IS THE POINT OF THE WHOLE PLATFORM. A catalog that starts at bronze can
answer "where did this number come from?" with a filename in `Files/landing/`
and stop. Contoso POS is not a CSV; it is a REST API with a published contract.
Contoso ERP is not a Parquet file; it is a Postgres table whose changes reach us
through a Kafka topic. Until those are entities, lineage is a half-truth.

Three source archetypes, three OpenMetadata service types, each native:

    Contoso POS   REST + OpenAPI   ->  API service        (collection, endpoints)
    Contoso ERP   Postgres         ->  Database service   (schema, table)
    ERP changes   Redpanda topic   ->  Messaging service  (topic)

DERIVED, NEVER AUTHORED TWICE. The API endpoints come from the OpenAPI spec the
vendor publishes and mokapi serves; the ERP columns come from the DDL that
created the table. A catalog whose semantics are retyped drifts from the
pipeline by the end of the first sprint.
"""

from __future__ import annotations

import base64
import json
import pathlib

import requests
import state
import yaml
from fabric import log

from sources import ERP_DB, ERP_TOPIC, POS_API

ROOT = pathlib.Path(__file__).resolve().parent.parent
OM = "http://localhost:8585/api/v1"
# OpenMetadata's seeded dev admin. Basic auth is not enough — the API wants a
# JWT, obtained by exchanging these at /users/login.
OM_USER = "admin@open-metadata.org"
OM_PASSWORD = "admin"

POS_SPEC = ROOT / "sources" / "contoso-pos" / "openapi.yaml"
ERP_DDL = ROOT / "sources" / "contoso-erp" / "schema.sql"

S = requests.Session()


def login() -> None:
    """Exchange the seeded admin for a JWT.

    Basic auth returns `401 Token not present` — a message that says what is
    missing rather than what to do, so it is worth stating here: OpenMetadata
    authenticates every API call with a bearer, and the password goes over
    /users/login base64-encoded.
    """
    r = S.post(
        f"{OM}/users/login",
        json={
            "email": OM_USER,
            "password": base64.b64encode(OM_PASSWORD.encode()).decode(),
        },
        timeout=60,
    )
    assert r.status_code == 200, (r.status_code, r.text[:300])
    S.headers["Authorization"] = f"Bearer {r.json()['accessToken']}"


def put(path: str, body: dict) -> dict:
    """OpenMetadata's PUT is create-or-update, so this is idempotent.

    Some endpoints answer 200 with an EMPTY body — lineage among them. Returning
    {} rather than raising keeps that legible, and every caller that needs a
    field from the response asserts on it, so an unexpectedly empty answer fails
    where it matters instead of here.
    """
    r = S.put(f"{OM}/{path}", json=body, timeout=60)
    assert r.status_code in (200, 201), (path, r.status_code, r.text[:300])
    return r.json() if r.content else {}


def register_pos() -> tuple[str, list[str]]:
    """Contoso POS as an API service, from the spec the vendor publishes."""
    spec = yaml.safe_load(POS_SPEC.read_text())
    svc = put(
        "services/apiServices",
        {
            "name": "contoso-pos",
            "serviceType": "Rest",
            "description": spec["info"]["description"].strip(),
            # `docURL`, not `openAPISchemaURL`: the field name differs by
            # OpenMetadata version and a wrong one is rejected at encrypt time
            # with a message that names the field, which is how this was found.
            "connection": {"config": {"type": "Rest", "docURL": POS_API}},
        },
    )
    assert svc.get("fullyQualifiedName"), ("api service", svc)
    collection = put(
        "apiCollections",
        {
            "name": "export",
            "displayName": spec["info"]["title"],
            "service": svc["fullyQualifiedName"],
            "endpointURL": f"{POS_API}/api/v1/export",
        },
    )

    assert collection.get("fullyQualifiedName"), ("api collection", collection)
    endpoints = []
    for route, methods in spec["paths"].items():
        for method, op in methods.items():
            ep = put(
                "apiEndpoints",
                {
                    "name": op["operationId"],
                    "displayName": op.get("summary", op["operationId"]),
                    "apiCollection": collection["fullyQualifiedName"],
                    "endpointURL": f"{POS_API}{route}",
                    "requestMethod": method.upper(),
                },
            )
            endpoints.append(ep["fullyQualifiedName"])
    return svc["fullyQualifiedName"], endpoints


def register_erp() -> str:
    """Contoso ERP as a Database service, with columns from its own DDL."""
    put(
        "services/databaseServices",
        {
            "name": "contoso-erp",
            "serviceType": "Postgres",
            "description": "Contoso ERP — the finance master. A database that "
            "CHANGES, which is why it reaches us by CDC rather than as a file.",
            "connection": {
                "config": {
                    "type": "Postgres",
                    "username": "contoso",
                    "authType": {"password": "***"},
                    "hostPort": "erp-postgres:5432",
                    "database": ERP_DB,
                }
            },
        },
    )
    db = put(
        "databases",
        {"name": ERP_DB, "service": "contoso-erp"},
    )
    schema = put(
        "databaseSchemas",
        {"name": "erp", "database": db["fullyQualifiedName"]},
    )

    # Columns from the DDL that created the table — one definition, not two.
    ddl = ERP_DDL.read_text()
    marker = "CREATE TABLE IF NOT EXISTS erp.customer ("
    body = ddl.split(marker, 1)[1].split(");", 1)[0]
    columns = []
    for line in body.splitlines():
        parts = line.strip().rstrip(",").split()
        if len(parts) >= 2 and not parts[0].startswith("--"):
            pg = parts[1].lower()
            columns.append(
                {
                    "name": parts[0],
                    "dataType": {
                        "text": "STRING",
                        "integer": "INT",
                        "date": "DATE",
                    }.get(pg, "STRING"),
                }
            )
    assert columns, "no columns parsed from the ERP DDL"

    table = put(
        "tables",
        {
            "name": "customer",
            "databaseSchema": schema["fullyQualifiedName"],
            "columns": columns,
        },
    )
    return table["fullyQualifiedName"]


def register_topic() -> str:
    """The change stream as a Messaging service — the CDC hop made visible."""
    put(
        "services/messagingServices",
        {
            "name": "contoso-redpanda",
            "serviceType": "Kafka",
            "description": "Debezium publishes Contoso ERP's row changes here.",
            "connection": {
                "config": {"type": "Kafka", "bootstrapServers": "redpanda:9092"}
            },
        },
    )
    topic = put(
        "topics",
        {
            "name": ERP_TOPIC,
            "service": "contoso-redpanda",
            "partitions": 1,
            "description": "One message per row change: op c/u/d with before and "
            "after images. REPLICA IDENTITY FULL, so a delete carries the row it "
            "closed rather than only its key.",
        },
    )
    return topic["fullyQualifiedName"]


# --- the platform's own tables ------------------------------------------------
# A Fabric workspace maps to an OM database; the lakehouse and the warehouse are
# its schemas — which is what they are on the SQL surface too, so a catalog user
# and a SQL user see the same names.
FABRIC_SERVICE = "contoso-fabric"

# The medallion, in order. Each entry is (schema, table, upstreams) and the
# upstreams are what actually built it — derived from the pipeline, not from a
# diagram someone drew once.
# --- the semantic layer -----------------------------------------------------
#
# Entities and lineage say a number EXISTS and where it came from. They cannot
# say what it means, who it is for, or what it promises — which is the half a
# consumer actually needs. These four blocks add that half, and every one of
# them is derived from something the platform already has.
#
# WHAT IS DELIBERATELY ABSENT: prose. A business glossary describing what
# "revenue" means to Contoso would have to be typed here, and this file's own
# rule forbids that — it would drift from the pipeline by the end of the first
# sprint and nothing would notice. The terms below are defined by their SQL,
# which is derived and exact. Real prose belongs in a data contract next to the
# transform, and there is not one in this repository yet.
DOMAIN = "contoso-sales"
GLOSSARY = "Contoso Sales"

# The measures gold publishes, taken from gold/models/fct_daily_revenue.sql.
# A metric is a column PLUS how it aggregates, which is exactly what a column
# entity cannot express and what a report writer needs.
METRICS = [
    (
        "revenue",
        "SUM",
        "DOLLARS",
        "sum(o.amount)",
        "Money taken, summed over the order-line grain and grouped by trading day "
        "and country. The measure the semantic model serves to Power BI.",
    ),
    (
        "orders",
        "COUNT",
        "TRANSACTIONS",
        "count(*)",
        "Orders on a trading day, counted at order grain.",
    ),
    (
        "units",
        "SUM",
        "COUNT",
        "sum(o.quantity)",
        "Items sold, which moves independently of revenue when the mix changes.",
    ),
]

# dbt's generic tests, in OpenMetadata's ODCS vocabulary. The mapping is exact
# — `unique` IS duplicateValues == 0 — so this is a rename, not an
# interpretation. The tests that gate the build become the contract in the
# catalog, which is the same fact stated once.
DBT_TO_ODCS = {
    "unique": {"metric": "duplicateValues", "dimension": "uniqueness"},
    "not_null": {"metric": "nullValues", "dimension": "completeness"},
    "accepted_values": {"metric": "invalidValues", "dimension": "conformity"},
}

MEDALLION = [
    ("lakehouse", "bronze_customers", []),
    ("lakehouse", "bronze_orders", []),
    ("lakehouse", "bronze_erp_changes", []),
    ("lakehouse", "silver_customers", ["lakehouse.bronze_customers"]),
    ("lakehouse", "silver_orders", ["lakehouse.bronze_orders"]),
    ("lakehouse", "silver_quarantine_orders", ["lakehouse.bronze_orders"]),
    ("warehouse", "dim_customer", ["lakehouse.silver_customers"]),
    ("warehouse", "fct_orders", ["lakehouse.silver_orders"]),
    (
        "warehouse",
        "fct_daily_revenue",
        ["warehouse.dim_customer", "warehouse.fct_orders"],
    ),
]


def register_fabric(st: dict, lake_cols: dict, wh_cols: dict) -> dict[str, str]:
    """The lakehouse and warehouse tables, with columns read from what was built."""
    put(
        "services/databaseServices",
        {
            "name": FABRIC_SERVICE,
            "serviceType": "Mssql",
            "description": "The Fabric workspace: a Lakehouse reached through its "
            "SQL analytics endpoint, and a Warehouse. Both are T-SQL surfaces.",
            "connection": {
                "config": {
                    "type": "Mssql",
                    "scheme": "mssql+pyodbc",
                    "username": "entra",
                    "hostPort": "fabric-emulator:1433",
                    "database": st["workspace_name"],
                }
            },
        },
    )
    db = put("databases", {"name": st["workspace_name"], "service": FABRIC_SERVICE})
    fqns = {}
    for schema in ("lakehouse", "warehouse"):
        sch = put(
            "databaseSchemas",
            {"name": schema, "database": db["fullyQualifiedName"]},
        )
        for s_name, table, _ in MEDALLION:
            if s_name != schema:
                continue
            cols = (lake_cols if schema == "lakehouse" else wh_cols).get(table)
            assert cols, f"no columns discovered for {schema}.{table}"
            t = put(
                "tables",
                {
                    "name": table,
                    "databaseSchema": sch["fullyQualifiedName"],
                    "columns": cols,
                },
            )
            fqns[f"{schema}.{table}"] = t["fullyQualifiedName"]
    return fqns


def add_lineage(
    from_fqn: str, from_type: str, to_fqn: str, to_type: str, how: str = ""
) -> None:
    """`how` records HOW the movement is known, not merely that it happened.

    The emulator distinguishes an edge it WATCHED (its TDS front saw the engine
    accept the statement) from one a step REPORTED, and a catalog that flattens
    the two has discarded the only thing telling a consumer how far to trust the
    graph. Where the emulator recorded a producer, it is carried through
    verbatim; where this platform knows the mechanism itself — Debezium's change
    stream, an HTTP pull — it says so in its own words rather than borrowing a
    label that would imply the emulator observed it.
    """
    edge: dict = {
        "fromEntity": {"id": entity_id(from_type, from_fqn), "type": from_type},
        "toEntity": {"id": entity_id(to_type, to_fqn), "type": to_type},
    }
    if how:
        edge["lineageDetails"] = {"description": how}
    put("lineage", {"edge": edge})


def emulator_producers(st: dict) -> dict[tuple[str, str], str]:
    """What the emulator recorded, keyed by (source table, target table).

    The MEDALLION constant below declares the shape this platform intends. This
    reads the shape the emulator actually OBSERVED while the run happened — so a
    producer here is evidence, and a declared edge with no match is a hop
    nothing witnessed. Absent or unreachable, the catalog is still built; it
    just carries no provenance claim, which is the honest degradation.
    """
    from fabric import FABRIC_AUD, fabric, token

    try:
        path = f"/v1/workspaces/{st['workspace']}/lineage"
        r = fabric("GET", path, token(FABRIC_AUD))
        edges = r.json().get("value", [])
    except Exception as e:  # provenance is a bonus, not a gate
        log(f"  ! emulator lineage unavailable ({type(e).__name__}) — unlabelled")
        return {}
    out = {}
    for e in edges:
        src, dst = e.get("sourcePath", ""), e.get("targetPath", "")
        if src.startswith("Tables/") and dst.startswith("Tables/"):
            key = (src.split("/", 1)[1], dst.split("/", 1)[1])
            out[key] = e.get("producer") or "Copy"
    return out


def dbt_quality_rules() -> dict[str, list[dict]]:
    """gold/models/schema.yml → ODCS quality rules, per model.

    These tests already gate the build: `dbt build` fails when one breaks.
    Publishing them as a contract states the same guarantee where a consumer
    can read it, without a second place to keep in step.
    """
    path = ROOT / "gold" / "models" / "schema.yml"
    if not path.is_file():
        return {}
    spec = yaml.safe_load(path.read_text()) or {}
    out: dict[str, list[dict]] = {}
    for model in spec.get("models", []) or []:
        rules = []
        for col in model.get("columns", []) or []:
            for test in col.get("tests", []) or []:
                name = test if isinstance(test, str) else next(iter(test))
                mapped = DBT_TO_ODCS.get(name)
                if not mapped:
                    # `relationships` is referential integrity, which ODCS has
                    # no library metric for — it becomes a sql rule naming the
                    # join, rather than being dropped or forced onto a metric
                    # that means something else.
                    if name == "relationships":
                        arg = test[name]
                        rules.append(
                            {
                                "type": "sql",
                                "name": f"{col['name']}_resolves",
                                "column": col["name"],
                                "dimension": "consistency",
                                "description": f"dbt `relationships` — every "
                                f"{col['name']} matches "
                                f"{arg.get('to')}.{arg.get('field')}.",
                                "query": f"select count(*) from {{object}} where "
                                f"{col['name']} is null",
                                "mustBe": 0,
                            }
                        )
                    continue
                desc = f"dbt `{name}` on {model['name']}.{col['name']}."
                rule = {
                    "type": "library",
                    "column": col["name"],
                    "mustBe": 0,
                    "unit": "rows",
                    "description": desc,
                    **mapped,
                }
                if name == "accepted_values":
                    rule["validValues"] = test[name].get("values", [])
                rules.append(rule)
        if rules:
            out[model["name"]] = rules
    return out


# OpenMetadata's routes are not uniformly `{type}s`. Guessing works for most
# and silently 404s for the rest, so the exceptions are written down.
ROUTES = {
    "table": "tables",
    "topic": "topics",
    "apiEndpoint": "apiEndpoints",
    "dashboardDataModel": "dashboard/datamodels",
}


def entity_id(kind: str, fqn: str) -> str:
    route = ROUTES.get(kind)
    assert route, f"no OpenMetadata route known for {kind!r} — add it to ROUTES"
    r = S.get(f"{OM}/{route}/name/{fqn}", timeout=60)
    assert r.status_code == 200, (kind, fqn, r.status_code, r.text[:200])
    return r.json()["id"]


def discover_columns(st: dict) -> tuple[dict, dict]:
    """Column lists read from what was actually built, on both sides.

    Lakehouse: the Delta schema, through the same engine that wrote it.
    Warehouse: INFORMATION_SCHEMA, through the container that has the driver.
    Neither is typed out here — a catalog that restates a schema is a second
    definition, and the two drift.
    """
    import spark as sparkmod

    from gold import in_dbt_container

    spark = sparkmod.session()
    base = sparkmod.lakehouse_uri(st["workspace"], st["lakehouse"])
    lake = {}
    for schema, table, _ in MEDALLION:
        if schema != "lakehouse":
            continue
        df = spark.read.format("delta").load(f"{base}/Tables/{table}")
        lake[table] = [
            {"name": f.name, "dataType": _om_type(f.dataType.simpleString())}
            for f in df.schema.fields
        ]

    rc = in_dbt_container("--entrypoint", "python", "dbt", "/tools/columns.py")
    assert rc == 0, f"warehouse column discovery failed: exit {rc}"
    raw = json.loads((ROOT / "gold" / "_columns.json").read_text())
    wh = {
        t: [{"name": c["name"], "dataType": _om_type(c["type"])} for c in cols]
        for t, cols in raw.items()
    }
    return lake, wh


def _om_type(native: str) -> str:
    """Map an engine type name onto OpenMetadata's vocabulary."""
    n = native.lower()
    if n.startswith(("int", "bigint", "smallint", "long")):
        return "INT"
    if n.startswith(("double", "float", "decimal", "numeric", "real")):
        return "DOUBLE"
    if n.startswith(("date",)):
        return "DATE"
    if n.startswith(("timestamp", "datetime")):
        return "TIMESTAMP"
    if n.startswith(("bool",)):
        return "BOOLEAN"
    return "STRING"


def _pbi_service() -> str:
    """A dashboard service to hang the semantic model on.

    The model is a Power BI artifact, so it belongs to a dashboard service
    rather than a database one — which is also how a real Fabric workspace
    presents it.
    """
    put(
        "services/dashboardServices",
        {
            "name": "contoso-powerbi",
            "serviceType": "PowerBI",
            "connection": {
                "config": {
                    "type": "PowerBI",
                    "clientId": "contoso",
                    "clientSecret": "***",
                    "tenantId": "contoso",
                }
            },
        },
    )
    return "contoso-powerbi"


def main() -> int:
    login()
    r = S.get(f"{OM}/system/version", timeout=30)
    assert r.status_code == 200, (
        f"OpenMetadata is not reachable — `make govern` starts it ({r.status_code})"
    )

    st = state.load()
    pos_svc, endpoints = register_pos()
    erp_table = register_erp()
    topic = register_topic()

    lake_cols, wh_cols = discover_columns(st)
    tables = register_fabric(st, lake_cols, wh_cols)

    # --- the semantic layer, before the lineage that will reference it -------
    put(
        "domains",
        {
            "name": DOMAIN,
            "displayName": "Contoso Sales",
            "domainType": "Consumer-aligned",
            "description": "Sales across Contoso's point-of-sale and ERP systems, "
            "conformed to one customer and one order grain.",
        },
    )
    put(
        "dataProducts",
        {
            "name": "contoso-sales-star",
            "displayName": "Contoso Sales star",
            "domains": [DOMAIN],
            "description": "The star served from the Warehouse and the semantic model "
            "over it — the layer downstream consumers may depend on.",
        },
    )
    put(
        "glossaries",
        {
            "name": GLOSSARY,
            "description": "Measures gold publishes, defined by the SQL that computes "
            "them. Prose definitions belong in a data contract beside "
            "the transform; this repository does not have one yet, and "
            "inventing the meaning here would put it in a second place.",
        },
    )
    for name, mtype, unit, expr, desc in METRICS:
        put(
            "glossaryTerms",
            {
                "name": name,
                "glossary": GLOSSARY,
                "description": f"{desc}\n\nComputed as `{expr}` in "
                f"gold/models/fct_daily_revenue.sql.",
            },
        )
        put(
            "metrics",
            {
                "name": name,
                "description": desc,
                "metricType": mtype,
                "unitOfMeasurement": unit,
                "domains": [DOMAIN],
                "granularity": "DAY",
                "metricExpression": {"language": "SQL", "code": expr},
            },
        )
    log(
        f"semantics: domain {DOMAIN!r}, 1 data product, {len(METRICS)} measures "
        f"as both glossary terms and metrics"
    )

    # --- contracts: the dbt tests, where a consumer can read them ------------
    contracts = dbt_quality_rules()
    n_rules = 0
    for model, rules in sorted(contracts.items()):
        fqn = tables.get(f"warehouse.{model}")
        if not fqn:
            continue
        put(
            "dataContracts",
            {
                "name": f"{model}-contract",
                "domains": [DOMAIN],
                "entity": {"id": entity_id("table", fqn), "type": "table"},
                "description": f"The guarantees `dbt build` enforces on {model}. "
                f"Derived from gold/models/schema.yml — the tests that "
                f"gate the build ARE the contract.",
                "odcsQualityRules": rules,
            },
        )
        n_rules += len(rules)
    log(
        f"contracts: {len(contracts)} DataContract(s) carrying {n_rules} rule(s) "
        f"from dbt's own tests"
    )

    observed = emulator_producers(st)

    # The hop that did not exist before: the vendor's change stream feeds the
    # ERP table's changes into the platform. Upstream of bronze, and therefore
    # upstream of every number the platform reports.
    add_lineage(
        erp_table,
        "table",
        topic,
        "topic",
        how="Debezium change stream — the platform's own connector, not "
        "an emulator observation",
    )

    # And the rest of the chain, so a number in the semantic model traces all
    # the way back to a vendor rather than to a file that appeared in landing.
    edges = 1
    add_lineage(
        topic,
        "topic",
        tables["lakehouse.bronze_erp_changes"],
        "table",
        how="Consumed from the change stream by ingest_erp_cdc",
    )
    edges += 1
    for ep in endpoints:
        for bronze in ("lakehouse.bronze_customers", "lakehouse.bronze_orders"):
            add_lineage(
                ep,
                "apiEndpoint",
                tables[bronze],
                "table",
                how="Pulled over HTTP by ingest_pos and landed verbatim",
            )
            edges += 1
    labelled = 0
    for schema, table, upstreams in MEDALLION:
        for up in upstreams:
            producer = observed.get((up.split(".", 1)[1], table))
            how = (
                f"Fabric {producer} — observed by the emulator"
                if producer
                else "Declared by the platform; the emulator recorded no edge"
            )
            labelled += 1 if producer else 0
            add_lineage(
                tables[up], "table", tables[f"{schema}.{table}"], "table", how=how
            )
            edges += 1
    dataset = st.get("dataset")
    if dataset:
        model = put(
            "dashboard/datamodels",
            {
                "name": "ContosoRevenue",
                "service": _pbi_service(),
                "dataModelType": "PowerBIDataModel",
                "columns": [
                    {"name": c, "dataType": "STRING"}
                    for c in ("OrderDate", "Country", "Orders", "Units", "Revenue")
                ],
            },
        )
        if model.get("fullyQualifiedName"):
            add_lineage(
                tables["warehouse.fct_daily_revenue"],
                "table",
                model["fullyQualifiedName"],
                "dashboardDataModel",
            )
            edges += 1

    catalogued = {
        "lineage_edges": edges,
        "fabric_tables": sorted(tables),
        "api_service": pos_svc,
        "api_endpoints": endpoints,
        "erp_table": erp_table,
        "topic": topic,
    }
    (ROOT / "catalog.json").write_text(json.dumps(catalogued, indent=2))
    state.save(catalog=sorted(catalogued))

    log(
        f"catalogued source systems: {pos_svc} ({len(endpoints)} endpoints), "
        f"{erp_table}, {topic}"
    )
    log(
        f"catalogued the medallion: {len(tables)} tables, {edges} lineage edges "
        f"({labelled} carrying a producer the emulator observed)"
    )
    # Direction matters and is easy to state backwards: Debezium READS the ERP
    # table and PUBLISHES to the topic, so data flows table -> topic. An edge
    # drawn the other way would claim the platform writes to the vendor — and
    # the first version of this log line said exactly that, while the edge
    # itself was correct.
    log(
        f"lineage: {erp_table} -> {topic} — the vendor is a node now, "
        f"not a filename in Files/landing"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
