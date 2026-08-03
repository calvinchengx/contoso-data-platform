-- Order grain, after silver's dedupe and quarantine.
--
-- INNER JOIN deliberately: an order whose customer is unknown has no place in
-- a star keyed by customer. Dropping it silently would be the problem, so the
-- schema test asserts the row count survives.
select
    o.order_id,
    o.customer_id,
    o.product_id,
    o.order_date,
    o.channel,
    o.quantity,
    o.unit_price,
    o.amount
from {{ source('silver', 'silver_orders') }} o
