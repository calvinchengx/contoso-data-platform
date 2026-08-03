"""Verify silver is queryable through the lakehouse SQL analytics endpoint.

In real Fabric a Lakehouse's SQL analytics endpoint exposes its Delta tables as
read-only T-SQL automatically — there is nothing to trigger. The emulator does
that reflection when the lakehouse database is first connected to, so this step
is a CONNECT AND VERIFY on both targets: gold reads silver by three-part name,
and this is what proves it can before dbt tries.

Runs in the dbt image because it needs the ODBC driver, which is the one thing
this platform deliberately does not install on a contributor's machine.
"""

import os
import sys
import time

import pyodbc

LAKEHOUSE = os.environ["LAKEHOUSE_ID"]
HOST = os.environ["DBT_HOST"]
PORT = os.environ.get("DBT_PORT", "1433")
TOKEN = os.environ["DBT_ACCESS_TOKEN"]

# The token goes in via SQL_COPT_SS_ACCESS_TOKEN — the ODBC mechanism Fabric
# uses for Entra auth, encoded the way the driver expects.
SQL_COPT_SS_ACCESS_TOKEN = 1256


def attrs(token: str) -> dict:
    raw = token.encode("utf-16-le")
    return {SQL_COPT_SS_ACCESS_TOKEN: len(raw).to_bytes(4, "little") + raw}


def main() -> int:
    dsn = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={HOST},{PORT};"
        f"DATABASE={LAKEHOUSE};Encrypt=no;TrustServerCertificate=yes"
    )
    # The first connect makes the emulator create and start the per-item
    # database, which can be slow. Retry rather than sleep: a fixed wait either
    # flakes on a loaded machine or wastes time on a fast one.
    last = None
    for _ in range(40):
        try:
            with pyodbc.connect(dsn, attrs_before=attrs(TOKEN), timeout=15) as c:
                rows = (
                    c.cursor().execute("SELECT COUNT(*) FROM silver_orders").fetchone()
                )
                print(
                    f"==> silver visible through the SQL endpoint: {rows[0]:,} orders",
                    flush=True,
                )
                return 0
        except Exception as exc:
            last = exc
            time.sleep(3)
    print(f"silver never became queryable: {last}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
