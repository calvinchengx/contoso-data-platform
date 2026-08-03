"""Dump the Warehouse's real column list, for the catalog to derive from.

INFORMATION_SCHEMA, not a hand-written list: the catalog must describe what was
actually built. Runs in the dbt image because reading a Warehouse means ODBC.
"""

import json
import os
import pathlib
import sys

import pyodbc

SQL_COPT_SS_ACCESS_TOKEN = 1256
OUT = pathlib.Path("/gold/_columns.json")


def main() -> int:
    raw = os.environ["DBT_ACCESS_TOKEN"].encode("utf-16-le")
    dsn = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={os.environ['DBT_HOST']},{os.environ.get('DBT_PORT', '1433')};"
        f"DATABASE={os.environ['DBT_DATABASE']};Encrypt=no;TrustServerCertificate=yes"
    )
    attrs = {SQL_COPT_SS_ACCESS_TOKEN: len(raw).to_bytes(4, "little") + raw}
    tables: dict[str, list[dict]] = {}
    with pyodbc.connect(dsn, attrs_before=attrs, timeout=30) as c:
        for name, col, typ in (
            c.cursor()
            .execute(
                "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE "
                "FROM INFORMATION_SCHEMA.COLUMNS "
                "ORDER BY TABLE_NAME, ORDINAL_POSITION"
            )
            .fetchall()
        ):
            tables.setdefault(name, []).append({"name": col, "type": typ})
    OUT.write_text(json.dumps(tables))
    print(f"==> warehouse columns: {len(tables)} tables", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
