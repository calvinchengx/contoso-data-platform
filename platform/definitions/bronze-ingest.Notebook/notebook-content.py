# Fabric notebook source
#
# THIS FILE IS A FABRIC NOTEBOOK. Its bytes are uploaded verbatim as the
# `notebook-content.py` part of a Notebook item, and Fabric's own notebook
# format is exactly this: a `# Fabric notebook source` header followed by
# sections delimited by `# CELL ****`. Nothing converts it and nothing
# generates it — what runs on Spark is the file you are reading.
#
# WHY BRONZE IS A NOTEBOOK NOW. It always claimed to be one: "Spark reads
# `abfs://…/Files/landing/…` itself, which is what a Fabric notebook or a Spark
# Job Definition does". But it ran in the platform's own process over Spark
# Connect, and NO FABRIC TENANT EXPOSES A SPARK CONNECT ENDPOINT — so the step
# could not have run in production at all. The transform is unchanged; what
# changed is that Fabric now decides where it runs.
#
# Inside a notebook `spark` is ambient: Fabric's pool binds it to a session that
# already carries the workspace identity and the attached lakehouse. So this file
# never builds a session, and building a second one inside a notebook is the
# thing that rule exists to prevent.
#
# BRONZE PARSES AND NOTHING MORE. No dedupe, no conforming, no quarantine —
# those are silver's job, and doing them here would destroy the only copy of what
# the vendor actually sent.

# CELL ********************

# The parameters cell. Real Fabric would override these per run through the
# job's `executionData.parameters`; the emulator implements no parameter
# override, so the platform substitutes them into this cell before publishing
# (see bronze.py). The placeholders are never valid ids, so a notebook published
# without substitution fails loudly on its first read instead of quietly
# resolving somewhere else.
WORKSPACE = "@@WORKSPACE@@"
LAKEHOUSE = "@@LAKEHOUSE@@"
DAY = "@@DAY@@"

# The real Fabric scheme, on both targets: the ENGINE resolves this, not the
# client, and Sail is configured against the emulator's storage endpoint.
BASE = f"abfs://{WORKSPACE}@onelake.dfs.fabric.microsoft.com/{LAKEHOUSE}"
LANDING = f"{BASE}/Files/landing"
TABLES = f"{BASE}/Tables"

# The Contoso Web page schemas, rendered from `web_schema.py` and substituted in.
#
# WHY SUBSTITUTED RATHER THAN IMPORTED. A notebook cannot import a sibling module
# out of `platform/`, and shipping `web_schema.py` as an extra definition part
# would not put it on the engine's path either. Inlining a COPY of the field
# names here would be worse than either: a test already pins those names to
# `sources/contoso-web/openapi.yaml`, and a second copy the test cannot see is
# exactly how a renamed vendor field turns into a column of nulls that every
# downstream count agrees with. So the module stays the single source and the
# rendered DDL travels through the same mechanism as the ids.
WEB_CUSTOMER_DDL = "@@WEB_CUSTOMER@@"
WEB_PRODUCT_DDL = "@@WEB_PRODUCT@@"
WEB_ORDER_DDL = "@@WEB_ORDER@@"

# The declared leaf names, for the "did anything parse at all" check below.
# Substituted from the same module, so this cannot disagree with the DDL above.
#
# `.split(",")` on what LOOKS like a literal, and ruff's SIM905 is right about the
# syntax and wrong about this file: the literal is a placeholder, replaced with a
# comma-joined list before publishing, so a list literal cannot be written here.
WEB_CUSTOMER_FIELDS = "@@WEB_CUSTOMER_FIELDS@@".split(",")  # noqa: SIM905
WEB_PRODUCT_FIELDS = "@@WEB_PRODUCT_FIELDS@@".split(",")  # noqa: SIM905
WEB_ORDER_FIELDS = "@@WEB_ORDER_FIELDS@@".split(",")  # noqa: SIM905

# CELL ********************

from pyspark.sql import functions as F

# What this notebook touches, recorded AS IT HAPPENS.
#
# Lineage in Fabric is reported by the engine, never inferred by the service from
# the code it was handed — so something has to observe the movements, and the
# only thing that can is the notebook's own IO. A read/write set declared by the
# publishing step drifts from the code the moment either changes; this one
# cannot, because it IS the code.
LINEAGE = []


def save(df, name: str) -> int:
    df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(
        f"{TABLES}/{name}"
    )
    LINEAGE.append(("write", f"Tables/{name}"))
    return df.count()


def landed(path: str) -> str:
    LINEAGE.append(("read", f"Files/landing/{path}"))
    return f"{LANDING}/{path}"


# CELL ********************

# --- Contoso POS -----------------------------------------------------------
# Every column stays a string. The vendor's CSV is text on the wire, and
# inferring types here would make bronze an interpretation rather than a copy —
# silver is where meaning gets assigned.
#
# A DIRECTORY, not a file. The vendor's export is paged, so landing holds
# `part-0001.csv … part-000N.csv` and the engine reads them as one dataset. This
# is the shape Spark wants anyway — parts are what it writes — so the paged
# vendor and the distributed reader agree without anything in between having to
# reassemble 170 MB in one process's memory.
#
# Every part repeats the header, which is why `header=True` stays correct across
# a directory rather than turning row one of each part into data.
customers = (
    spark.read.option("header", True)
    .option("inferSchema", False)
    .csv(landed(f"contoso_pos/{DAY}/customers/"))
)
n_cust = save(customers, "bronze_customers")

orders = spark.read.json(landed(f"contoso_pos/{DAY}/orders/"))
n_ord = save(orders, "bronze_orders")

# CELL ********************

# --- Contoso Web -----------------------------------------------------------
# This vendor ships JSON ARRAYS, not the JSON Lines the POS orders feed sends.
# Same reader, different vendor, different dialect — which is the whole reason
# there is more than one source.
#
# THE ENGINE'S JSON READER IS NDJSON-ONLY, and it does not say so. Both
# `multiLine` and `wholetext` are accepted and then ignored; that is measured,
# not assumed. Reading the POS export — 255,000 JSON Lines across a handful of
# files — with `wholetext=True` returns 255,000 rows, one per line, when an
# honoured option would have returned one per file. An array page handed to
# .json() therefore fails with `Expected JSON record to be an object, found
# Array`.
#
# So the page is read as TEXT and parsed with from_json, which keeps the parse in
# the engine. Pulling the pages through the platform's process to json.loads them
# would also work, and would put one machine back in the data path — the shape
# this step exists to reject.


def read_json_array(path: str, ddl: str):
    page = F.from_json("value", ddl)
    return spark.read.text(path).select(F.explode(page).alias("r")).select("r.*")


# ONE LINE PER PAGE is what makes .text() equal to "one row per page", and
# ingest_web asserts it as the bytes arrive — so a vendor that starts
# pretty-printing fails there, naming the cause, rather than here with a column
# of nulls that every count below would agree with.
web_customers = read_json_array(
    landed(f"contoso_web/{DAY}/customers/"), WEB_CUSTOMER_DDL
)
n_web_cust = save(web_customers, "bronze_web_customers")

web_products = read_json_array(landed(f"contoso_web/{DAY}/products/"), WEB_PRODUCT_DDL)
n_web_prod = save(web_products, "bronze_web_products")

# NESTED, and left that way. `lines` stays an array here because bronze is the
# record of what arrived; exploding it to an order-line grain is a decision, and
# it belongs where the decision is visible.
web_orders = read_json_array(landed(f"contoso_web/{DAY}/orders/"), WEB_ORDER_DDL)
n_web_ord = save(web_orders, "bronze_web_orders")

# CELL ********************

# --- Contoso Reference -----------------------------------------------------
# The group data office's master data, and the only vendor here that is not an
# operational system. Parquet, so the reader needs no options at all — the file
# carries its own schema, which is the whole reason a data office publishing
# definitions would choose it.
fx = spark.read.parquet(landed(f"contoso_reference/{DAY}/fx_rates.parquet"))
n_fx = save(fx, "bronze_fx_rates")

hierarchy = spark.read.parquet(
    landed(f"contoso_reference/{DAY}/product_hierarchy.parquet")
)
n_hier = save(hierarchy, "bronze_product_hierarchy")

# --- Contoso ERP -----------------------------------------------------------
changes = spark.read.parquet(landed(f"contoso_erp/{DAY}/changes.parquet"))
n_erp = save(changes, "bronze_erp_changes")

# CELL ********************

# --- what the run OBSERVED -------------------------------------------------
# Computed here because these quantities exist only inside the transform, and
# GRADED in bronze.py against the generators. A notebook does not import a test
# fixture: the expected counts belong to the harness, and a query grading its own
# output confirms nothing.

distinct_cust = customers.select("customer_id").distinct().count()
distinct_ord = orders.select("order_id").distinct().count()
customer_columns = len(customers.columns)

# The NESTING survived. A reader that flattened or dropped `lines` would still
# land the right row count, and the loss would only surface much later as an
# order with no items.
web_orders_has_lines = "lines" in web_orders.columns

# A declared field that no longer matches the vendor's JSON parses as NULL rather
# than raising, and every count above would still agree — the row count comes
# from the array's length, not from its contents. So check the columns actually
# carry something. `limit(1)` because the question is "anything at all", not "how
# many".
blank = []
for tname, tdf, tfields in (
    ("bronze_web_customers", web_customers, WEB_CUSTOMER_FIELDS),
    ("bronze_web_products", web_products, WEB_PRODUCT_FIELDS),
    ("bronze_web_orders", web_orders, WEB_ORDER_FIELDS),
):
    for c in tfields:
        if tdf.filter(F.col(c).isNotNull()).limit(1).count() == 0:
            blank.append(f"{tname}.{c}")

# The overlap the resolution problem depends on is really there: web accounts
# share emails with POS customers, and neither vendor knows it.
web_emails = {r["email"] for r in web_customers.select("email").distinct().collect()}
pos_emails = {
    r["email"] for r in customers.select("email").distinct().collect() if r["email"]
}
shared = len(web_emails & pos_emails)

fx_currencies = fx.select("currency").distinct().count()
fx_published_days = fx.select("rate_date").distinct().count()
# The CALENDAR span, against which the published days are sparse. FX is
# published on trading days only, so this table is missing every weekend — and
# that absence is the whole reason gold has to carry the last rate forward
# instead of joining on the date.
fx_calendar_span = fx.selectExpr(
    "datediff(max(rate_date), min(rate_date)) + 1 AS days"
).collect()[0]["days"]
departments = hierarchy.select("department").distinct().count()

# CELL ********************

# ONE ROW, written as Delta like everything else.
#
# WHY A TABLE AND NOT AN EXIT VALUE. Real Fabric exposes no exit value for a
# REST-submitted run, so a notebook cannot return anything to the step that
# submitted it. A Delta table is portable by construction, and materialising run
# metrics is something real teams do anyway. The emulator DOES surface an exit
# value, and using it would have been green here and unavailable in production —
# the same trap as the Spark Connect endpoint this step just stopped depending
# on.
#
# An explicit schema rather than dict inference: one row is the case where
# inference buys nothing and engines disagree most.
metrics = spark.createDataFrame(
    [
        (
            n_cust,
            distinct_cust,
            customer_columns,
            n_ord,
            distinct_ord,
            n_web_cust,
            n_web_prod,
            n_web_ord,
            web_orders_has_lines,
            ",".join(sorted(blank)),
            shared,
            n_fx,
            fx_currencies,
            fx_published_days,
            int(fx_calendar_span),
            n_hier,
            departments,
            n_erp,
        )
    ],
    "bronze_customers long, distinct_customers long, customer_columns long, "
    "bronze_orders long, distinct_orders long, "
    "bronze_web_customers long, bronze_web_products long, bronze_web_orders long, "
    "web_orders_has_lines boolean, blank_columns string, shared_emails long, "
    "bronze_fx_rates long, fx_currencies long, fx_published_days long, "
    "fx_calendar_span long, "
    "bronze_product_hierarchy long, departments long, bronze_erp_changes long",
)
save(metrics, "bronze_ingest_metrics")

print(
    f"bronze: {n_cust} POS customer rows ({distinct_cust} distinct), "
    f"{n_ord} POS order events, {n_web_cust} web accounts, "
    f"{n_web_ord} web orders, {n_erp} ERP change events"
)
