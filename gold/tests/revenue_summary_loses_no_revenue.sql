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
-- A cent, not zero: these are floating-point sums over half a million rows, so
-- demanding bit equality would fail on arithmetic rather than on loss.
where abs(detail.total - summary.total) > 0.01
