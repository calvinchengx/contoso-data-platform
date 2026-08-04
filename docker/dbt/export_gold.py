"""Export gold from the Warehouse over TDS, for the semantic model to carry.

Runs in the dbt image because reading a Fabric Warehouse means ODBC, which this
platform deliberately does not install on a contributor's machine.

In real Fabric a production model would use **Direct Lake** and read gold in
place — no export at all. This is an IMPORT model: the rows are selected here
and embedded in the model definition, which is a real Fabric pattern and the
one the emulator can serve today. The distinction matters for lineage: an
import model carries no binding back to its source, so the step that publishes
it has to say what it read.
"""

import json
import os
import pathlib
import sys

import pyodbc

WAREHOUSE = os.environ["DBT_DATABASE"]
HOST = os.environ["DBT_HOST"]
PORT = os.environ.get("DBT_PORT", "1433")
TOKEN = os.environ["DBT_ACCESS_TOKEN"]
OUT = pathlib.Path("/gold/_export.json")

SQL_COPT_SS_ACCESS_TOKEN = 1256


def attrs(token: str) -> dict:
    raw = token.encode("utf-16-le")
    return {SQL_COPT_SS_ACCESS_TOKEN: len(raw).to_bytes(4, "little") + raw}


def main() -> int:
    dsn = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={HOST},{PORT};"
        f"DATABASE={WAREHOUSE};Encrypt=no;TrustServerCertificate=yes"
    )
    with pyodbc.connect(dsn, attrs_before=attrs(TOKEN), timeout=30) as c:
        fact = [
            {
                "OrderDate": str(r[0])[:10],
                "Country": r[1],
                "Orders": int(r[2]),
                "Units": int(r[3]),
                "Revenue": float(r[4]),
            }
            for r in c.cursor()
            .execute(
                "SELECT order_date, country, orders, units, revenue "
                "FROM fct_daily_revenue"
            )
            .fetchall()
        ]
        dim = [
            {"CustomerId": r[0], "Name": r[1], "Country": r[2]}
            for r in c.cursor()
            .execute("SELECT customer_id, name, country FROM dim_customer")
            .fetchall()
        ]
        # THE MANAGEMENT REPORTING AGGREGATE, and the reason it needs no
        # dimension tables beside it: fct_revenue_summary already carries its
        # own attributes. A report writer slices it directly, so the model does
        # not need a relationship to reach a fiscal period or a segment — which
        # is exactly what the Revenue/Customer pair does need, and does badly.
        reporting = [
            {
                "FiscalYearLabel": r[0],
                "FiscalQuarterLabel": r[1],
                "Department": r[2],
                "ProductSegment": r[3],
                "CustomerSegment": r[4],
                "Country": r[5],
                "Orders": int(r[6]),
                "Units": int(r[7]),
                "RevenueUsd": float(r[8]),
                "RevenueAtCarriedRate": float(r[9]),
            }
            for r in c.cursor()
            .execute(
                "SELECT fiscal_year_label, fiscal_quarter_label, department, "
                "product_segment, customer_segment, country, orders, units, "
                "revenue_usd, revenue_at_carried_rate FROM fct_revenue_summary"
            )
            .fetchall()
        ]

    assert fact and dim and reporting, (len(fact), len(dim), len(reporting))
    OUT.write_text(
        json.dumps({"Revenue": fact, "Customer": dim, "Reporting": reporting})
    )
    print(
        f"==> exported gold: {len(fact):,} revenue rows, {len(dim):,} customers, "
        f"{len(reporting):,} reporting rows",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
