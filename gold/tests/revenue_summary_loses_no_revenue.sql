-- The aggregate must account for EVERY dollar in the fact, to the cent.
--
-- fct_revenue_summary inner-joins three dimensions. Any one of them failing to
-- match removes revenue from the pack, and the result still balances against
-- itself — every subtotal adds up, the grand total is simply smaller than the
-- business earned. That is the failure mode a P&L reviewer cannot see and this
-- test exists to make impossible.
--
-- A singular test rather than a column test because the claim is a relationship
-- BETWEEN two models, which `not_null` and `relationships` cannot express.
with detail as (
    select sum(amount_usd) as total from {{ ref('fct_orders') }}
),

summary as (
    select sum(revenue_usd) as total from {{ ref('fct_revenue_summary') }}
)

select
    detail.total  as detail_total,
    summary.total as summary_total
from detail
cross join summary
-- A cent, not zero: these are floating-point sums over a quarter of a million
-- rows, so demanding bit equality would fail on arithmetic rather than on loss.
where abs(detail.total - summary.total) > 0.01
