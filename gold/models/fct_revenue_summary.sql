-- THE MANAGEMENT REPORTING AGGREGATE: revenue by financial year, by what was
-- sold, and by who bought it — now across BOTH selling systems.
--
-- This is the table `fct_daily_revenue` could not be. That one is day x country,
-- which answers "how are we trading" and nothing management accounting asks.
-- The axes here are the ones that appear on a P&L pack:
--
--   FISCAL PERIOD     from dim_date, on Contoso's 1 April financial year — so
--                     July trading reports as FY27 Q2, and the storefront sales
--                     that fall on 30 June once their UTC offsets are applied
--                     report as FY27 Q1. Two quarters, from one month of
--                     calendar trading, which is exactly why the fiscal
--                     calendar is not a relabelling of the calendar one.
--   PRODUCT SEGMENT   from the group data office's rollup, plus "Unallocated"
--                     for the SKUs the storefront sells that nobody published.
--   CUSTOMER SEGMENT  from the POS system, via the resolved party. NULL for
--                     web-only shoppers, reported as its own line rather than
--                     bucketed — see below.
--   CHANNEL SYSTEM    which business sold it. Kept because "revenue is up" and
--                     "online revenue is up" are different sentences.
--
-- NET IS THE HEADLINE. The storefront cancels about 5% of its orders and the
-- POS system has no such concept, so `revenue_usd` excludes cancellations and
-- `cancelled_revenue_usd` reports them alongside. A pack that showed only gross
-- would overstate the business by the cancellation rate; one that showed only
-- net would hide it. A singular test asserts the two still account for every
-- dollar in fct_sales.
--
-- LEFT JOIN TO THE PARTY, and this is the one that matters. `marketing_segment`
-- is NULL for the 18,000 people who have only ever shopped online, because only
-- the POS system segments customers. An inner join would have removed their
-- revenue from the pack entirely — a fifth of the customer base vanishing with
-- every subtotal still adding up correctly.
select
    d.fiscal_year,
    d.fiscal_year_label,
    d.fiscal_quarter,
    d.fiscal_quarter_label,
    s.channel_system,
    p.department,
    p.product_segment,
    -- Named rather than left NULL at the reporting boundary: a pack row reading
    -- "Unsegmented" is a fact about the business, where a blank is a fact about
    -- the query. The NULL is preserved in dim_party for anyone who needs it.
    coalesce(pt.marketing_segment, 'Unsegmented') as customer_segment,
    coalesce(pt.country, 'Unknown')               as country,
    count(*)                                      as sale_lines,
    sum(case when s.is_cancelled = 1 then 0 else s.quantity end) as units,
    sum(case when s.is_cancelled = 1 then 0 else s.amount_usd end) as revenue_usd,
    sum(case when s.is_cancelled = 1 then s.amount_usd else 0 end)
        as cancelled_revenue_usd,
    -- The share of NET revenue priced at a carried-forward FX rate. FX is
    -- published on trading days only; see silver_notebook.py for the rule.
    sum(case
            when s.is_cancelled = 0 and s.rate_is_carried = 1 then s.amount_usd
            else 0
        end) as revenue_at_carried_rate
from {{ ref('fct_sales') }} s
join {{ ref('dim_date') }} d
  on d.date_key = cast(s.order_date as date)
join {{ ref('dim_product') }} p
  on p.product_id = s.product_id
left join {{ ref('dim_party') }} pt
  on pt.party_key = s.party_key
group by
    d.fiscal_year,
    d.fiscal_year_label,
    d.fiscal_quarter,
    d.fiscal_quarter_label,
    s.channel_system,
    p.department,
    p.product_segment,
    coalesce(pt.marketing_segment, 'Unsegmented'),
    coalesce(pt.country, 'Unknown')
