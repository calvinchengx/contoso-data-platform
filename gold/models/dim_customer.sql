-- The customer dimension: a projection of silver, not a second source of truth.
--
-- WIDER THAN IT WAS, and the two new columns are the point. Silver carries 101
-- columns per customer and this projected four of them, so revenue could be
-- sliced by country and by nothing else. `marketing_segment` and `loyalty_tier`
-- were already conformed in silver — the reporting axis management asks for
-- first was present all along and simply never surfaced.
--
-- STILL A PROJECTION. Nothing is computed here that silver has not already
-- conformed, because a dimension deriving its own values is a second answer to
-- a question silver already answered.
select
    customer_id,
    name,
    email,
    country,
    -- A CUSTOMER segment — "premium", "lapsed", "new". Not dim_product's
    -- `product_segment`, which is a different axis of the same star. Both are
    -- in fct_revenue_summary and joining the wrong one still yields a number,
    -- so the names are kept apart rather than both being `segment`.
    marketing_segment,
    loyalty_tier
from {{ source('silver', 'silver_customers') }}
