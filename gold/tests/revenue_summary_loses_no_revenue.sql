-- The aggregate must account for EVERY dollar in the unified fact, to the cent.
--
-- fct_revenue_summary joins three dimensions and splits revenue into net and
-- cancelled. Any dimension failing to match removes revenue from the pack, and
-- the result still balances against itself: every subtotal adds up, the grand
-- total is simply smaller than the business earned. That is the failure a P&L
-- reviewer cannot see, and this test exists to make it impossible.
--
-- NET + CANCELLED, not net alone — the storefront cancels about 5% of its
-- orders, and checking only the headline would let the write-offs disappear.
with detail as (
    select sum(amount_usd) as total from {{ ref('fct_sales') }}
),

summary as (
    select sum(revenue_usd) + sum(cancelled_revenue_usd) as total
    from {{ ref('fct_revenue_summary') }}
)

select
    detail.total  as detail_total,
    summary.total as summary_total
from detail
cross join summary
-- EXACTLY EQUAL, with no tolerance at all. This used to allow a cent, because
-- the sums were binary floats over half a million rows and demanding bit
-- equality would have failed on arithmetic rather than on loss. Money is
-- decimal(19,4) now, so both sides are exact and any difference is real —
-- which makes this the strictest form of the test and the whole point of the
-- type change.
where detail.total <> summary.total
