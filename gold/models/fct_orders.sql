-- Order grain, after silver's dedupe and quarantine.
--
-- The comment about an INNER JOIN to the customer dimension is now a comment
-- about two joins, and they are deliberately different shapes.
--
-- CURRENCY IS NOT COSMETIC. Orders are taken in USD, GBP, SGD and EUR, and
-- until now `amount` was summed across all four as though it were one number.
-- That total was not a currency — it was the sum of four, which is a quantity
-- with no unit and no meaning. `amount_usd` is the figure a P&L can use.
--
-- LEFT JOIN TO FX, NOT INNER, and this is the important one. An inner join
-- would silently DROP any order it could not price, and the query would still
-- succeed with a smaller, entirely plausible total. Silver's carry-forward
-- means every calendar day in the FX span has a rate, so nothing should be
-- unpriced — and `amount_usd` carries a not_null test in schema.yml, so if that
-- ever stops being true the build fails and names the column rather than
-- quietly reporting less revenue.
select
    o.order_id,
    o.customer_id,
    o.product_id,
    o.order_date,
    o.channel,
    -- Carried through so the decision is available and visible. POS statuses
    -- are shipped / pending / error; this vendor has no notion of a
    -- cancellation, so nothing here nets one out. The storefront does report
    -- cancellations, and its orders reach this star with identity resolution —
    -- netting them belongs there, with the data that supports it.
    o.status,
    o.currency,
    o.quantity,
    o.unit_price,
    o.amount,
    fx.rate_to_usd,
    -- Whether this order was priced at a QUOTED rate or an ASSUMED one. FX is
    -- published on trading days only, so every weekend order is converted at
    -- the preceding Friday's rate. That is the correct treatment and it is
    -- still an assumption, so it stays visible per row instead of dissolving
    -- into the total.
    fx.rate_is_carried,
    o.amount * fx.rate_to_usd as amount_usd
from {{ source('silver', 'silver_orders') }} o
-- JOINED AS TEXT, both sides. Every date in this platform travels as ISO text
-- because bronze keeps what the vendor sent, and silver_fx_daily formats its
-- dates back to match. Casting one side to `date` here would clash with the
-- other, which is exactly what it did: a Spark DateType surfaces through the
-- SQL analytics endpoint as bigint, and the build failed with `Operand type
-- clash: date is incompatible with bigint`.
left join {{ source('silver', 'silver_fx_daily') }} fx
  on fx.currency = o.currency
 and fx.rate_date = o.order_date
