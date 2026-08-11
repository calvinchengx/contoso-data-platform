-- No money column may be a binary float. Checked against the warehouse's own
-- catalog, because no amount of row checking can see this.
--
-- WHY THIS TEST EXISTS. `dim_product` unions the published catalogue with the
-- SKUs nobody published, and the unpublished side declared its price as
-- `cast(null as float)`. A UNION takes the wider type, so one `float` on a
-- column of NULLs silently demoted every real price back to floating point.
-- Every row still held the right number, every other test passed, and the
-- column had stopped being money. It was found by reading the reflected type,
-- which is the only thing that can find it.
--
-- MATCHED BY NAME, deliberately. A test listing the columns it knows about
-- protects the columns it knows about; the next money column somebody adds is
-- exactly the one that would slip through. Anything named like money has to BE
-- money, and a column that trips this and is genuinely not money should be
-- renamed rather than exempted.
-- ORDERING IS LOAD-BEARING, and this test did not have it. A singular test
-- that names no model has no place in dbt's graph, so it gets scheduled
-- arbitrarily — this one ran 4th of 61, BEFORE the models it checks were
-- rebuilt, and passed against the previous run's tables. It reported green
-- while a float was reintroduced, which is the precise failure it exists to
-- prevent, one level up.
--
-- These refs are never read. `where 1 = 0` keeps the subquery empty and the
-- predicate below is always true, so they change no result — they exist to put
-- this test downstream of the models in the DAG.
with built as (
    select 1 as x from {{ ref('dim_product') }} where 1 = 0
    union all select 1 from {{ ref('fct_sales') }} where 1 = 0
    union all select 1 from {{ ref('fct_orders') }} where 1 = 0
    union all select 1 from {{ ref('fct_revenue_summary') }} where 1 = 0
    union all select 1 from {{ ref('fct_daily_revenue') }} where 1 = 0
)

-- UPPERCASE, because a Warehouse is case-sensitive. Fabric reports
-- `Latin1_General_100_BIN2_UTF8`, Microsoft's "default - case-sensitive (CS)
-- collation", so `information_schema.columns` is a different name from the view
-- that exists and the tenant answers `Invalid object name`. This read lowercase
-- and passed for months against an emulator whose databases inherited the
-- container's case-INsensitive collation; it would have failed the first time it
-- ran anywhere real. Emulator 0.21.0 adopted Fabric's collation and the mistake
-- surfaced locally, which is the only place it is cheap to fix.
--
-- The literals stay lowercase: those match VALUES (this warehouse's column names
-- and SQL Server's own type names), not identifiers.
select
    TABLE_NAME,
    COLUMN_NAME,
    DATA_TYPE
from INFORMATION_SCHEMA.COLUMNS
where (
        COLUMN_NAME like '%amount%'
        or COLUMN_NAME like '%price%'
        or COLUMN_NAME like '%revenue%'
        or COLUMN_NAME like '%rate_to%'
    )
    and DATA_TYPE <> 'decimal'
    and (select count(*) from built) = 0
