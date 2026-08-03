# Fabric notebook source
#
# THIS FILE IS A FABRIC NOTEBOOK. Its bytes are uploaded verbatim as the
# `notebook-content.py` part of a Notebook item, and Fabric's own notebook
# format is exactly this: a `# Fabric notebook source` header followed by
# sections delimited by `# CELL ****`. Nothing converts it and nothing
# generates it — what runs on Spark is the file you are reading.
#
# WHY A FILE AND NOT A STRING. The obvious way to publish a notebook is an
# f-string in the step that submits it. Then the transform is not Python as far
# as any tool is concerned: ruff does not lint it, ty does not check it, and a
# syntax error surfaces as a failed cell on a Spark engine rather than at
# `make lint`. Keeping it a real module costs one substitution (below) and buys
# the whole toolchain.
#
# WHY IT LOOKS DIFFERENT FROM THE OTHER STEPS. Inside a notebook `spark` is
# ambient — Fabric's Spark pool binds it to a session that already carries the
# workspace identity and the attached lakehouse. So this file never calls
# `spark.session()`; using the ambient session IS the rule, and building a
# second one inside a notebook is the thing the rule exists to prevent.
#
# The transform itself is unchanged from when it ran as a plain Spark Connect
# script, which is the claim being tested: the platform's transforms are
# notebook code, not scripts that resemble notebook code.

# CELL ********************

# The parameters cell. Real Fabric would override these per run through the
# job's `executionData.parameters`; the emulator does not implement parameter
# overrides, so the platform substitutes the ids into this cell before
# publishing (see silver.py). The placeholders are never valid ids, so a
# notebook published without substitution fails loudly on its first read
# instead of quietly resolving somewhere else.
WORKSPACE = "@@WORKSPACE@@"
LAKEHOUSE = "@@LAKEHOUSE@@"

# The real Fabric scheme, on both targets: the ENGINE resolves this, not the
# client, and Sail is configured against the emulator's storage endpoint.
TABLES = f"abfs://{WORKSPACE}@onelake.dfs.fabric.microsoft.com/{LAKEHOUSE}/Tables"

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

# CELL ********************

import json

from pyspark.sql import Window
from pyspark.sql import functions as F

# What this notebook touches, recorded AS IT HAPPENS.
#
# Lineage in Fabric is reported by the engine, never inferred by the service
# from the code it was handed — so something has to observe the movements, and
# the only thing that can is the notebook's own IO. The host reads this list
# after each cell and attributes what appeared to that cell.
#
# The alternative was for the publishing step to declare the read/write set up
# front, which is what it did first: every read was paired with every write and
# the graph gained `bronze_customers -> silver_orders`, a movement that never
# happened. A declared set drifts from the code the moment either changes; this
# one cannot, because it IS the code.
LINEAGE = []


def read(name):
    LINEAGE.append(("read", f"Tables/{name}"))
    return spark.read.format("delta").load(f"{TABLES}/{name}")


def save(df, name: str) -> int:
    df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(
        f"{TABLES}/{name}"
    )
    LINEAGE.append(("write", f"Tables/{name}"))
    return df.count()


# CELL ********************

# --- customers: dedupe, conform --------------------------------------------
# WIDE, deliberately. Silver is the conformed customer-360 and gold's
# dimensions are a projection of it, not the other way round — so the transform
# REPLACES two columns and keeps every other one.
conform = F.create_map([F.lit(x) for kv in COUNTRY.items() for x in kv])
country_key = F.upper(F.trim(F.col("country")))

c = read("bronze_customers")
customers = (
    c.withColumn(
        "_rn",
        F.row_number().over(Window.partitionBy("customer_id").orderBy("customer_id")),
    )
    .filter(F.col("_rn") == 1)
    .drop("_rn")
    # '' rather than NULL for "the vendor sent none": the missing-email cohort
    # has to stay identifiable, because it is the cohort that can never be
    # matched to an email-keyed system.
    .withColumn("email", F.lower(F.trim(F.coalesce(F.col("email"), F.lit("")))))
    .withColumn("country", F.coalesce(conform[country_key], country_key))
)
n_cust = save(customers, "silver_customers")

# CELL ********************

# --- orders: latest event wins, then split ---------------------------------
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

# CELL ********************

# What the notebook OBSERVED, handed back for the platform to grade.
#
# The assertions live in silver.py, not here, and the split is deliberate. A
# production notebook does not import a test fixture — the expected counts come
# from the generator wheel, which belongs to the harness and not to the
# transform. So the notebook reports what it saw and the platform compares that
# against what the vendor said it sent, which is also the rule about grading
# against a different source than the one that produced the number.
countries = sorted(
    {r["country"] for r in customers.select("country").distinct().collect()}
)

notebook_exit(
    json.dumps(
        {
            "silver_customers": n_cust,
            "silver_orders": n_ord,
            "silver_quarantine_orders": n_quar,
            "customer_columns": len(customers.columns),
            "countries": countries,
            "missing_email": customers.filter(F.col("email") == "").count(),
        }
    )
)
