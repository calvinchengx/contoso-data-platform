-- The operational aggregate: how trading moved, day by day, by country.
--
-- `revenue` HERE IS MIXED-CURRENCY and deliberately left that way. It sums
-- `amount` across USD, GBP, SGD and EUR, which is a quantity with no unit — but
-- it is the figure the fixture contract pins (source_system.EXPECTED_REVENUE),
-- and both the semantic model and the XMLA probe assert against it. Changing it
-- here would silently redefine what those two surfaces are checking.
--
-- SO IT IS NOT THE MANAGEMENT REPORTING TABLE. fct_revenue_summary is: revenue
-- in USD, on Contoso's 1 April financial year, by product segment and customer
-- segment. Use this one to watch trading; use that one to report.
select
    o.order_date,
    c.country,
    count(*)        as orders,
    sum(o.quantity) as units,
    sum(o.amount)   as revenue
from {{ ref('fct_orders') }} o
join {{ ref('dim_customer') }} c
  on o.customer_id = c.customer_id
group by o.order_date, c.country
