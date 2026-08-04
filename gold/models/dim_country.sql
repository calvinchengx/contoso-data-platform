-- The conformed geography dimension, and the reason it exists is a modelling
-- bug rather than a reporting request.
--
-- WHAT WAS WRONG. The semantic model related Revenue to Customer on `Country`:
--
--     Revenue[Country]  ->  Customer[Country]
--
-- Country is not a key of either table. There are three values across 100,000
-- customers and 84 revenue rows, so that relationship is many-to-many on a
-- three-member column. It answers a country total and can answer nothing
-- finer, because there is no path from an aggregated revenue row back to a
-- customer — and `fct_daily_revenue` has no customer key to build one from.
-- Worse, it is the kind of wrong that still returns a number.
--
-- WHAT THIS FIXES IT TO. Country becomes a dimension in its own right, with
-- one row per country, and every table that carries a country relates to it
-- MANY-TO-ONE — the ordinary direction, from the many side to a unique key:
--
--     Customer[Country]   ->  Country[Country]
--     Revenue[Country]    ->  Country[Country]
--     Reporting[Country]  ->  Country[Country]
--
-- A single country slicer now filters all three consistently, which is exactly
-- what a conformed dimension is for and exactly what the many-to-many could
-- not do.
--
-- DERIVED FROM THE PARTIES, so it lists the countries the business actually
-- has and cannot drift from them. dim_party spans both selling systems, and
-- silver has already conformed "United States" and "USA" alike to US — this
-- dimension inherits that work rather than repeating it.
with present as (
    select distinct country
    from {{ ref('dim_party') }}
    where country is not null
)

select
    country,
    -- The label a report writer puts on an axis. A business name for a code,
    -- stated once here rather than in every report that needs to show one.
    case country
        when 'US' then 'United States'
        when 'GB' then 'United Kingdom'
        when 'SG' then 'Singapore'
        else country
    end as country_name
from present
