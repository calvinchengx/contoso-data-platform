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


def add_lineage(from_fqn: str, from_type: str, to_fqn: str, to_type: str) -> None:
    put(
        "lineage",
        {
            "edge": {
                "fromEntity": {"id": entity_id(from_type, from_fqn), "type": from_type},
                "toEntity": {"id": entity_id(to_type, to_fqn), "type": to_type},
            }
        },
    )


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

    # The hop that did not exist before: the vendor's change stream feeds the
    # ERP table's changes into the platform. Upstream of bronze, and therefore
    # upstream of every number the platform reports.
    add_lineage(erp_table, "table", topic, "topic")

    # And the rest of the chain, so a number in the semantic model traces all
    # the way back to a vendor rather than to a file that appeared in landing.
    edges = 1
    add_lineage(topic, "topic", tables["lakehouse.bronze_erp_changes"], "table")
    edges += 1
    for ep in endpoints:
        for bronze in ("lakehouse.bronze_customers", "lakehouse.bronze_orders"):
            add_lineage(ep, "apiEndpoint", tables[bronze], "table")
            edges += 1
    for schema, table, upstreams in MEDALLION:
        for up in upstreams:
            add_lineage(tables[up], "table", tables[f"{schema}.{table}"], "table")
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
    log(f"catalogued the medallion: {len(tables)} tables, {edges} lineage edges")
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
