-- THE MANAGEMENT REPORTING AGGREGATE: revenue by financial year, by what was
-- sold, and by who bought it.
--
-- This is the table `fct_daily_revenue` could not be. That one is day x
-- country, which answers "how are we trading" and nothing management accounting
-- asks. The three axes here are the ones that appear on a P&L pack:
--
--   FISCAL PERIOD     from dim_date, on Contoso's 1 April financial year — so
--                     July 2026 reports as FY27 Q2 and not as Q3 of anything.
--   PRODUCT SEGMENT   from the group data office's rollup, SKU -> category ->
--                     department -> segment. Neither selling system knows it.
--   CUSTOMER SEGMENT  from silver's conformed customer, which carried it all
--                     along while the star projected four columns.
--
-- REVENUE IS IN USD, converted per order at that day's rate. Summing `amount`
-- across four currencies, as this platform did until now, produces a number
-- with no unit. `revenue_at_carried_rate` reports how much of the total was
-- priced at an assumed rate rather than a quoted one — FX is published on
-- trading days only, so weekend trading is always converted at Friday's rate,
-- and a P&L that cannot see how much of itself rests on that assumption is
-- hiding the one thing a reviewer would ask about.
--
-- INNER JOINS THROUGHOUT, and every one is covered by a schema test asserting
-- the grain and the totals survive. A dimension that failed to match would
-- otherwise remove revenue from this table silently, and the result would still
-- balance against itself.
select
    d.fiscal_year,
    d.fiscal_year_label,
    d.fiscal_quarter,
    d.fiscal_quarter_label,
    p.department,
    p.product_segment,
    c.marketing_segment as customer_segment,
    c.country,
    count(*)             as orders,
    sum(f.quantity)      as units,
    sum(f.amount_usd)    as revenue_usd,
    -- The share of the above that rests on a carried-forward FX rate. Reported
    -- rather than assumed away; see the note at the top.
    sum(case when f.rate_is_carried = 1 then f.amount_usd else 0 end)
        as revenue_at_carried_rate
from {{ ref('fct_orders') }} f
join {{ ref('dim_date') }} d
  on d.date_key = cast(f.order_date as date)
join {{ ref('dim_product') }} p
  on p.product_id = f.product_id
join {{ ref('dim_customer') }} c
  on c.customer_id = f.customer_id
group by
    d.fiscal_year,
    d.fiscal_year_label,
    d.fiscal_quarter,
    d.fiscal_quarter_label,
    p.department,
    p.product_segment,
    c.marketing_segment,
    c.country
