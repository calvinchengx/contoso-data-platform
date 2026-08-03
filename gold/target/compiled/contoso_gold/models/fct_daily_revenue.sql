-- The reporting aggregate the semantic model serves.
select
    o.order_date,
    c.country,
    count(*)        as orders,
    sum(o.quantity) as units,
    sum(o.amount)   as revenue
from [aee86aac-de4c-435a-b206-1b1e54f4eb07].[dbo].[fct_orders] o
join [aee86aac-de4c-435a-b206-1b1e54f4eb07].[dbo].[dim_customer] c
  on o.customer_id = c.customer_id
group by o.order_date, c.country