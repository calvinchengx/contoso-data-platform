-- The operational aggregate: how trading moved, day by day, by country.
--
-- `revenue` SUMS RAW `amount`, not the USD-denominated figure, and is left that
-- way deliberately: it is what the fixture contract pins
-- (source_system.EXPECTED_REVENUE), and both the semantic model and the XMLA
-- probe assert against it. Changing it would silently redefine what those two
-- surfaces check.
--
-- It happens to equal the USD total today, because Contoso POS stamps every
-- order `USD` and every rate is therefore 1.0. That is a property of the
-- current fixture, not a guarantee — which is precisely why the reporting
-- table below does its own conversion rather than assuming the two stay equal.
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
