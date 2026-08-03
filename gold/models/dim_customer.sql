-- The customer dimension: a projection of silver, not a second source of truth.
select
    customer_id,
    name,
    email,
    country
from {{ source('silver', 'silver_customers') }}
