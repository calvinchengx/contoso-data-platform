-- BOTH SELLING SYSTEMS, one grain, one currency.
--
-- This is the fact the reporting pack is built on. `fct_orders` remains the POS
-- system's own fact, keyed on its `customer_id` and pinned by the fixture
-- contract; this one is the unified view, keyed on the resolved `party_key` so
-- a shop sale and an online sale by the same person land on the same row of any
-- customer-level report.
--
-- LINE GRAIN, because that is the only grain both systems share. A POS order is
-- already one product; a storefront order is a basket, which silver flattened.
-- Reporting at order grain would mean either exploding one side or collapsing
-- the other, and collapsing loses the product.
--
-- CANCELLATIONS ARE CARRIED, NOT FILTERED. The storefront reports 5% of its
-- orders cancelled and the POS system has no such concept. Dropping them here
-- would make the number irrecoverable downstream; `is_cancelled` lets the pack
-- report net revenue as the headline and still show what was written off.
with pos as (
    select
        p.party_key,
        o.order_id                        as sale_id,
        o.product_id,
        o.order_date,
        'POS'                             as channel_system,
        cast(0 as bit)                    as is_cancelled,
        o.quantity,
        o.amount,
        o.currency
    from {{ ref('fct_orders') }} o
    -- INNER, and safe: every POS customer becomes a party, including the ones
    -- with no email. A row failing to match here would mean silver dropped a
    -- customer, which its own assertions forbid.
    join {{ ref('dim_party') }} p
      on p.pos_customer_id = o.customer_id
),

web as (
    select
        p.party_key,
        w.web_order_id                    as sale_id,
        w.product_id,
        w.order_date,
        'WEB'                             as channel_system,
        case when w.status = 'cancelled' then cast(1 as bit) else cast(0 as bit) end
                                          as is_cancelled,
        w.quantity,
        w.amount,
        w.currency
    from {{ source('silver', 'silver_web_order_lines') }} w
    -- Joined on the EMAIL-derived key, which is the only handle the storefront
    -- gives. Every web account became a party in silver, so this is total.
    join {{ ref('dim_party') }} p
      on p.email = w.email
     and p.in_web = 1
),

united as (
    select * from pos
    union all
    select * from web
)

select
    u.party_key,
    u.sale_id,
    u.product_id,
    u.order_date,
    u.channel_system,
    u.is_cancelled,
    u.quantity,
    u.amount,
    u.currency,
    fx.rate_to_usd,
    fx.rate_is_carried,
    u.amount * fx.rate_to_usd as amount_usd
from united u
-- LEFT, so an unpriceable sale is kept and caught by the not_null test on
-- amount_usd rather than silently removed from revenue. Silver's carry-forward
-- means every calendar day in the FX span has a rate — including 30 June, which
-- the storefront reaches once its UTC offsets are applied.
-- Text on both sides, for the reason fct_orders.sql sets out: a Delta DateType
-- reflects through the SQL analytics endpoint as bigint on the pinned build, so
-- every date in this platform travels as ISO text. silver_notebook.py holds the
-- condition for unwinding it.
left join {{ source('silver', 'silver_fx_daily') }} fx
  on fx.currency = u.currency
 and fx.rate_date = u.order_date
