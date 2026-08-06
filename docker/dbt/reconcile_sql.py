"""Compute the report's headline figures straight from the Warehouse.

The other half of the reconciliation: `platform/reconcile.py` asks the SAME
question of the semantic model in DAX, and the two answers have to agree. This
side is deliberately the dumbest possible SQL — no measures, no model, no
semantic layer — because a check where both sides share machinery only proves
the machinery is consistent with itself.

Runs in the dbt image for the same reason columns.py does: reading a Warehouse
means ODBC, and the driver lives in one image rather than on a laptop.
"""

import json
import os
import pathlib
import sys

import pyodbc

SQL_COPT_SS_ACCESS_TOKEN = 1256
OUT = pathlib.Path("/gold/_reconcile_sql.json")

# One row per fiscal quarter: the axis the pack is actually read on. Cast to
# float only at the very end — the warehouse stores money as decimal(19,4), and
# summing in float first would introduce the error this gate exists to detect.
QUERY = """
SELECT fiscal_quarter_label,
       SUM(revenue_usd)            AS revenue_usd,
       SUM(cancelled_revenue_usd)  AS cancelled_revenue_usd
FROM   dbo.fct_revenue_summary
GROUP BY fiscal_quarter_label
ORDER BY fiscal_quarter_label
"""


def main() -> int:
    raw = os.environ["DBT_ACCESS_TOKEN"].encode("utf-16-le")
    dsn = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={os.environ['DBT_HOST']},{os.environ.get('DBT_PORT', '1433')};"
        f"DATABASE={os.environ['DBT_DATABASE']};Encrypt=no;TrustServerCertificate=yes"
    )
    attrs = {SQL_COPT_SS_ACCESS_TOKEN: len(raw).to_bytes(4, "little") + raw}
    rows = []
    with pyodbc.connect(dsn, attrs_before=attrs, timeout=30) as c:
        for quarter, revenue, cancelled in c.cursor().execute(QUERY).fetchall():
            rows.append(
                {
                    "quarter": quarter,
                    "revenue_usd": str(revenue),
                    "cancelled_revenue_usd": str(cancelled),
                }
            )
    OUT.write_text(json.dumps(rows))
    print(f"==> warehouse figures: {len(rows)} fiscal quarter(s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
