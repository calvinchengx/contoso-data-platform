"""The three gold aggregates compare_products.py holds every runtime to.

The FAMILY's claim is that each platform builds the same product. Two green
pipelines do not establish that; the same numbers do. `scripts/compare_products.py`
in contoso-data-product compares fabric against databricks (and now airflow),
and it needs one JSON per runtime.

Deliberately the dumbest possible SQL, for the same reason reconcile_sql.py is:
a comparison whose two sides share machinery only proves the machinery agrees
with itself. This reads the star directly.

Runs in the dbt image because reading a Warehouse means ODBC, and the driver
lives in one image rather than on a laptop.
"""

import json
import os
import pathlib
import sys

import pyodbc

SQL_COPT_SS_ACCESS_TOKEN = 1256
OUT = pathlib.Path("/gold/_snapshot.json")

# coalesce, so an empty star reports 0 rather than NULL. compare_products then
# REFUSES an all-zero snapshot -- a runtime that built nothing must not compare
# equal to another that built nothing.
QUERY = """
SELECT COALESCE(SUM(revenue_usd), 0),
       COALESCE(SUM(cancelled_revenue_usd), 0),
       COALESCE(SUM(sale_lines), 0)
FROM   dbo.fct_revenue_summary
"""


def main() -> int:
    raw = os.environ["DBT_ACCESS_TOKEN"].encode("utf-16-le")
    warehouse = os.environ["DBT_DATABASE"]
    dsn = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={os.environ['DBT_HOST']},{os.environ.get('DBT_PORT', '1433')};"
        f"DATABASE={warehouse};Encrypt=no;TrustServerCertificate=yes"
    )
    attrs = {SQL_COPT_SS_ACCESS_TOKEN: len(raw).to_bytes(4, "little") + raw}
    with pyodbc.connect(dsn, attrs_before=attrs, timeout=30) as c:
        revenue, cancelled, lines = c.cursor().execute(QUERY).fetchone()

    snapshot = {
        # Strings, not floats: the warehouse stores money as decimal(19,4), and
        # comparing runtimes through float would make a precision difference
        # look like agreement -- or a rounding artefact look like a discrepancy.
        "revenue_usd": str(revenue),
        "cancelled_revenue_usd": str(cancelled),
        "sale_lines": str(lines),
        # The contract NAMES, from this platform's own gold project. If a test
        # is missing here and present elsewhere, the runtimes are not asserting
        # the same things and compare_products says so.
        "contracts": sorted(p.stem for p in pathlib.Path("/gold/tests").glob("*.sql")),
        "runtime": "fabric",
        "catalog": warehouse,
    }
    OUT.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    print(
        f"==> gold snapshot: revenue_usd={snapshot['revenue_usd']} "
        f"sale_lines={snapshot['sale_lines']} "
        f"contracts={len(snapshot['contracts'])}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
