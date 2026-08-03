"""Bronze → silver: dedupe, conform, quarantine — distributed.

Three rules, and each exists because bronze deliberately violates it:

  * the vendor repeats rows, so customers are deduped on the key
  * orders arrive at-least-once, so the LATEST event per order wins — ranked by
    the vendor's own sequence, never "pick any", which is correct only by luck
    and silently wrong the day a redelivery carries a different status
  * malformed orders are QUARANTINED, not dropped: a row nobody can price is
    still a row someone has to reconcile

Window functions, not a client-side pass. Every row stays in the engine, so
this is the same code at 100,000 customers and at a hundred million — which is
the property a single-node engine cannot offer however convenient it is.
"""

from __future__ import annotations

import spark as sparkmod
import state
from fabric import log
from pyspark.sql import Window
from pyspark.sql import functions as F

# Silver's own business rule, written out rather than derived from the
# generator's COUNTRY_VARIANTS. Importing that mapping would make the
# conformance assertion agree with itself, and a new variant appearing upstream
# would silently conform instead of failing.
COUNTRY = {
    "US": "US",
    "USA": "US",
    "U.S.": "US",
    "UNITED STATES": "US",
    "GB": "GB",
    "GBR": "GB",
    "UK": "GB",
    "U.K.": "GB",
    "UNITED KINGDOM": "GB",
    "SG": "SG",
    "SGP": "SG",
    "SINGAPORE": "SG",
}


def main() -> int:
    import source_system as src

    st = state.load()
    spark = sparkmod.session()
    base = sparkmod.lakehouse_uri(st["workspace"], st["lakehouse"])
    tables = f"{base}/Tables"

    def read(name):
        return spark.read.format("delta").load(f"{tables}/{name}")

    def save(df, name: str) -> int:
        df.write.format("delta").mode("overwrite").option(
            "overwriteSchema", "true"
        ).save(f"{tables}/{name}")
        return df.count()

    # --- customers: dedupe, conform ----------------------------------------
    # WIDE, deliberately. Silver is the conformed customer-360 and gold's
    # dimensions are a projection of it, not the other way round — so the
    # transform REPLACES two columns and keeps every other one.
    conform = F.create_map([F.lit(x) for kv in COUNTRY.items() for x in kv])
    country_key = F.upper(F.trim(F.col("country")))

    c = read("bronze_customers")
    customers = (
        c.withColumn(
            "_rn",
            F.row_number().over(
                Window.partitionBy("customer_id").orderBy("customer_id")
            ),
        )
        .filter(F.col("_rn") == 1)
        .drop("_rn")
        # '' rather than NULL for "the vendor sent none": the missing-email
        # cohort has to stay identifiable, because it is the cohort that can
        # never be matched to an email-keyed system.
        .withColumn("email", F.lower(F.trim(F.coalesce(F.col("email"), F.lit("")))))
        .withColumn("country", F.coalesce(conform[country_key], country_key))
    )
    n_cust = save(customers, "silver_customers")

    # --- orders: latest event wins, then split -----------------------------
    latest = Window.partitionBy("order_id").orderBy(F.col("event_seq").desc())
    o = (
        read("bronze_orders")
        .withColumn("_rn", F.row_number().over(latest))
        .filter(F.col("_rn") == 1)
        .drop("_rn")
    )

    bad = (F.col("quantity") <= 0) | F.col("unit_price").isNull()
    clean = o.filter(~bad).withColumn("amount", F.col("quantity") * F.col("unit_price"))
    quarantine = o.filter(bad)

    n_ord = save(clean, "silver_orders")
    n_quar = save(quarantine, "silver_quarantine_orders")

    countries = {r["country"] for r in customers.select("country").distinct().collect()}
    missing_email = customers.filter(F.col("email") == "").count()

    assert n_cust == src.EXPECTED_SILVER_CUSTOMERS, (
        n_cust,
        src.EXPECTED_SILVER_CUSTOMERS,
    )
    assert n_ord == src.EXPECTED_SILVER_ORDERS, (n_ord, src.EXPECTED_SILVER_ORDERS)
    assert n_quar == src.EXPECTED_QUARANTINED, (n_quar, src.EXPECTED_QUARANTINED)
    assert countries == src.EXPECTED_COUNTRIES, (
        sorted(countries),
        src.EXPECTED_COUNTRIES,
    )
    # Width, not just row count: gold's dimensions project from here, so a
    # narrow silver is a correctness failure every row count would pass over.
    assert len(customers.columns) == src.EXPECTED_CUSTOMER_COLUMNS, (
        len(customers.columns),
        src.EXPECTED_CUSTOMER_COLUMNS,
    )
    # The unmatchable cohort survives. It is the reason a resolution step that
    # claims 100% is lying, and dropping it here would erase the evidence.
    assert missing_email > 0, "the missing-email cohort vanished"

    state.save(
        silver={
            "silver_customers": n_cust,
            "silver_orders": n_ord,
            "silver_quarantine_orders": n_quar,
        }
    )
    log(
        f"silver: {n_cust:,} customers x {len(customers.columns)} cols, "
        f"{n_ord:,} orders, {n_quar:,} quarantined, countries {sorted(countries)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
