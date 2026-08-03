-- The customer dimension: a projection of silver, not a second source of truth.
select
    customer_id,
    name,
    email,
    country
from [a254f50a-6ef9-4312-82f4-1e31e2aae7d9].[dbo].[silver_customers]