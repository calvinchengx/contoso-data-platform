"""Landing → bronze, read by the ENGINE, not by this process.

Bronze parses and nothing more. No dedupe, no conforming, no quarantine —
those are silver's job, and doing them here would destroy the only copy of what
the vendor actually sent.

THE ENGINE READS LANDING DIRECTLY. An earlier version pulled 170 MB out of
OneLake through this process to parse it client-side. That works and it is the
wrong shape: it puts one machine in the data path, and it does not scale past
what that machine can hold. Spark reads `abfs://…/Files/landing/…` itself,
which is what a Fabric notebook or Spark Job Definition does.

Nothing here is emulator-aware. The paths are real Fabric OneLake URIs and the
session comes from spark.py — ambient inside a Fabric notebook, Spark Connect
outside one.
"""

from __future__ import annotations

import connections
import spark as sparkmod
import state
import web_schema
from fabric import FABRIC_AUD, log, token
from pyspark.sql import functions as F


def main() -> int:
    import erp_system as erp
    import source_system as src
    import web_store as web

    st = state.load()
    day = st["landing_day"]
    spark = sparkmod.session()
    base = sparkmod.lakehouse_uri(st["workspace"], st["lakehouse"])
    landing = f"{base}/Files/landing"
    tables = f"{base}/Tables"

    def save(df, name: str) -> int:
        df.write.format("delta").mode("overwrite").option(
            "overwriteSchema", "true"
        ).save(f"{tables}/{name}")
        return df.count()

    # --- Contoso POS -------------------------------------------------------
    # Every column stays a string. The vendor's CSV is text on the wire, and
    # inferring types here would make bronze an interpretation rather than a
    # copy — silver is where meaning gets assigned.
    # A DIRECTORY, not a file. The vendor's export is paged, so landing holds
    # `part-0001.csv … part-000N.csv` and the engine reads them as one dataset.
    # This is the shape Spark wants anyway — parts are what it writes — so the
    # paged vendor and the distributed reader agree without anything in between
    # having to reassemble 170 MB in one process's memory.
    #
    # Every part repeats the header, which is why `header=True` stays correct
    # across a directory rather than turning row one of each part into data.
    customers = (
        spark.read.option("header", True)
        .option("inferSchema", False)
        .csv(f"{landing}/contoso_pos/{day}/customers/")
    )
    n_cust = save(customers, "bronze_customers")

    orders = spark.read.json(f"{landing}/contoso_pos/{day}/orders/")
    n_ord = save(orders, "bronze_orders")

    # --- Contoso Web -------------------------------------------------------
    # This vendor ships JSON ARRAYS, not the JSON Lines the POS orders feed
    # sends. Same reader, different vendor, different dialect — which is the
    # whole reason there is more than one source.
    #
    # THE ENGINE'S JSON READER IS NDJSON-ONLY, and it does not say so. Both
    # `multiLine` and `wholetext` are accepted and then ignored; that is
    # measured, not assumed. Reading the POS export — 255,000 JSON Lines across
    # a handful of files — with `wholetext=True` returns 255,000 rows, one per
    # line, when an honoured option would have returned one per file. An array
    # page handed to .json() therefore fails with `Expected JSON record to be an
    # object, found Array`.
    #
    # So the page is read as TEXT and parsed with from_json, which keeps the
    # parse in the engine. Pulling the pages through this process to json.loads
    # them would also work, and would put one machine back in the data path —
    # the shape this module's docstring exists to reject.
    def read_json_array(path: str, fields: dict[str, object]):
        page = F.from_json("value", web_schema.array_of(fields))
        return spark.read.text(path).select(F.explode(page).alias("r")).select("r.*")

    # ONE LINE PER PAGE is what makes .text() equal to "one row per page", and
    # ingest_web asserts it as the bytes arrive — so a vendor that starts
    # pretty-printing fails there, naming the cause, rather than here with a
    # column of nulls that every count below would agree with.
    web_customers = read_json_array(
        f"{landing}/contoso_web/{day}/customers/", web_schema.WEB_CUSTOMER
    )
    n_web_cust = save(web_customers, "bronze_web_customers")

    web_products = read_json_array(
        f"{landing}/contoso_web/{day}/products/", web_schema.WEB_PRODUCT
    )
    n_web_prod = save(web_products, "bronze_web_products")

    # NESTED, and left that way. `lines` stays an array here because bronze is
    # the record of what arrived; exploding it to an order-line grain is a
    # decision, and it belongs where the decision is visible.
    web_orders = read_json_array(
        f"{landing}/contoso_web/{day}/orders/", web_schema.WEB_ORDER
    )
    n_web_ord = save(web_orders, "bronze_web_orders")

    # --- Contoso ERP -------------------------------------------------------
    changes = spark.read.parquet(f"{landing}/contoso_erp/{day}/changes.parquet")
    n_erp = save(changes, "bronze_erp_changes")

    # --- what bronze must have preserved -----------------------------------
    # The vendor repeats a share of its rows. Bronze holding MORE rows than
    # distinct customers is the property silver's dedupe exists to fix — and if
    # bronze had already deduped, silver would pass its own assertions while
    # testing nothing.
    distinct_cust = customers.select("customer_id").distinct().count()
    assert distinct_cust == src.EXPECTED_SILVER_CUSTOMERS, (
        distinct_cust,
        src.EXPECTED_SILVER_CUSTOMERS,
    )
    assert n_cust > distinct_cust, (
        f"bronze holds {n_cust:,} rows for {distinct_cust:,} customers — the "
        f"vendor's redeliveries are missing, so silver's dedupe has nothing to do"
    )
    assert len(customers.columns) == src.EXPECTED_CUSTOMER_COLUMNS, (
        len(customers.columns),
        src.EXPECTED_CUSTOMER_COLUMNS,
    )

    # Orders arrive at-least-once, so bronze must exceed the distinct order
    # count that silver settles on.
    distinct_ord = orders.select("order_id").distinct().count()
    assert n_ord > distinct_ord, (n_ord, distinct_ord)

    # --- what the web vendor must have preserved ---------------------------
    assert n_web_cust == web.N_WEB_CUSTOMERS, (n_web_cust, web.N_WEB_CUSTOMERS)
    assert n_web_ord == web.N_WEB_ORDERS, (n_web_ord, web.N_WEB_ORDERS)
    assert n_web_prod == len(web.PRODUCTS), (n_web_prod, len(web.PRODUCTS))
    # The NESTING survived. A reader that flattened or dropped `lines` would
    # still land the right row count, and the loss would only surface much
    # later as an order with no items.
    assert "lines" in web_orders.columns, web_orders.columns
    # A declared field that no longer matches the vendor's JSON parses as NULL
    # rather than raising, and every count above would still agree — the row
    # count comes from the array's length, not from its contents. So check the
    # columns actually carry something. `limit(1)` because the question is
    # "anything at all", not "how many".
    for tname, tdf, tfields in (
        ("bronze_web_customers", web_customers, web_schema.WEB_CUSTOMER),
        ("bronze_web_products", web_products, web_schema.WEB_PRODUCT),
        ("bronze_web_orders", web_orders, web_schema.WEB_ORDER),
    ):
        blank = [
            c for c in tfields if tdf.filter(F.col(c).isNotNull()).limit(1).count() == 0
        ]
        assert not blank, (
            f"{tname}: {blank} parsed entirely NULL — the schema declared in "
            f"web_schema.py no longer matches what Contoso Web sends"
        )
    # And the overlap the resolution problem depends on is really there: web
    # accounts share emails with POS customers, and neither vendor knows it.
    web_emails = {
        r["email"] for r in web_customers.select("email").distinct().collect()
    }
    pos_emails = {
        r["email"] for r in customers.select("email").distinct().collect() if r["email"]
    }
    shared = len(web_emails & pos_emails)
    assert shared > 0, (
        "no web account shares an email with a POS customer — the overlap that "
        "makes identity resolution a real problem is missing"
    )

    assert n_erp == erp.EXPECTED_ERP_CHANGE_EVENTS, (
        n_erp,
        erp.EXPECTED_ERP_CHANGE_EVENTS,
    )

    # The landing→bronze hop, reported because nothing else can see it. Spark
    # read `abfs://…` directly, so the emulator watched bytes leave OneLake and
    # bytes arrive, with nothing tying one to the other — and without this the
    # vendor nodes the ingest steps name would hang off landing paths that no
    # later edge mentions, leaving the source systems floating beside the
    # medallion rather than feeding it. One move per table: the ERP change
    # stream did not produce the customers table.
    ftok = token(FABRIC_AUD)
    lake = st["lakehouse"]
    connections.announce(
        ftok,
        st["workspace"],
        "bronze",
        "landing",
        [
            {
                "reads": [{"itemId": lake, "path": f"Files/landing/{src_path}"}],
                "writes": [{"itemId": lake, "path": f"Tables/{table}"}],
            }
            for src_path, table in (
                (f"contoso_pos/{day}/customers", "bronze_customers"),
                (f"contoso_pos/{day}/orders", "bronze_orders"),
                (f"contoso_web/{day}/customers", "bronze_web_customers"),
                (f"contoso_web/{day}/products", "bronze_web_products"),
                (f"contoso_web/{day}/orders", "bronze_web_orders"),
                (f"contoso_erp/{day}/changes.parquet", "bronze_erp_changes"),
            )
        ],
    )

    state.save(
        bronze={
            "bronze_customers": n_cust,
            "bronze_orders": n_ord,
            "bronze_web_customers": n_web_cust,
            "bronze_web_products": n_web_prod,
            "bronze_web_orders": n_web_ord,
            "bronze_erp_changes": n_erp,
        }
    )
    log(
        f"bronze: {n_cust:,} POS customer rows ({distinct_cust:,} distinct, "
        f"{len(customers.columns)} cols), {n_ord:,} POS order events "
        f"({distinct_ord:,} distinct), {n_web_cust:,} web accounts "
        f"({shared:,} sharing an email with POS), {n_web_ord:,} web orders "
        f"nested over {n_web_prod} products, {n_erp:,} ERP change events"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
