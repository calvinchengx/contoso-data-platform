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


def entity_id(kind: str, fqn: str) -> str:
    r = S.get(f"{OM}/{kind}s/name/{fqn}", timeout=60)
    assert r.status_code == 200, (kind, fqn, r.status_code, r.text[:200])
    return r.json()["id"]


def main() -> int:
    login()
    r = S.get(f"{OM}/system/version", timeout=30)
    assert r.status_code == 200, (
        f"OpenMetadata is not reachable — `make govern` starts it ({r.status_code})"
    )

    pos_svc, endpoints = register_pos()
    erp_table = register_erp()
    topic = register_topic()

    # The hop that did not exist before: the vendor's change stream feeds the
    # ERP table's changes into the platform. Upstream of bronze, and therefore
    # upstream of every number the platform reports.
    add_lineage(erp_table, "table", topic, "topic")

    catalogued = {
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
