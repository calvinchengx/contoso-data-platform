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

import spark as sparkmod
import state
from fabric import log


def main() -> int:
    import erp_system as erp
    import source_system as src

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

    assert n_erp == erp.EXPECTED_ERP_CHANGE_EVENTS, (
        n_erp,
        erp.EXPECTED_ERP_CHANGE_EVENTS,
    )

    state.save(
        bronze={
            "bronze_customers": n_cust,
            "bronze_orders": n_ord,
            "bronze_erp_changes": n_erp,
        }
    )
    log(
        f"bronze: {n_cust:,} customer rows ({distinct_cust:,} distinct, "
        f"{len(customers.columns)} cols), {n_ord:,} order events "
        f"({distinct_ord:,} distinct), {n_erp:,} ERP change events"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
