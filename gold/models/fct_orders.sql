-- Order grain, after silver's dedupe and quarantine.
--
-- The comment about an INNER JOIN to the customer dimension is now a comment
-- about two joins, and they are deliberately different shapes.
--
-- CURRENCY, AND AN HONEST NOTE ABOUT WHAT IT DOES TODAY. The reference vendor
-- publishes rates for USD, GBP, SGD and EUR, but Contoso POS stamps every
-- order `USD` — so every rate looked up here is 1.0 and `amount_usd` currently
-- equals `amount` to the cent. The conversion is an identity, not a fix.
--
-- IT IS STILL WORTH BUILDING, for two reasons that do not depend on today's
-- fixture. The storefront is a second selling system with its own currencies
-- and reaches this star with identity resolution, at which point summing raw
-- `amount` silently becomes the sum of several currencies — a quantity with no
-- unit. And `rate_is_carried` is already meaningful: it records which orders
-- fell on a day the market did not publish, which is true of 28.8% of trading
-- regardless of what the rate turned out to be.
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
    -- CAST BACK TO MONEY after the multiply. `decimal(19,4) * decimal(19,6)`
    -- widens to a scale of 10, and carrying ten decimal places through every
    -- downstream sum would be false precision dressed as rigour — the amount is
    -- money again the moment the conversion is done.
    cast(o.amount * fx.rate_to_usd as decimal(19,4)) as amount_usd
from {{ source('silver', 'silver_orders') }} o
-- DATE TO DATE. `rate_date` is a real date; `order_date` is text because bronze
-- keeps what the vendor sent, so the cast is on the order side where the
-- conversion actually belongs.
--
-- This was `nvarchar = nvarchar` until fabric-emulator v0.16.0, because a Delta
-- DateType reflected through the SQL analytics endpoint as bigint and any cast
-- to `date` here clashed with it. Fixed in f26c182 and re-measured against the
-- released build before this line moved.
left join {{ source('silver', 'silver_fx_daily') }} fx
  on fx.currency = o.currency
 and fx.rate_date = cast(o.order_date as date)
