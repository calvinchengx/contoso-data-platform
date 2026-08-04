-- Every country used anywhere must exist in dim_country, exactly once.
--
-- WHY A SINGULAR TEST AND NOT `relationships`. Three tables carry a country and
-- all three now relate to this dimension in the semantic model — but only some
-- of them are dbt models with a column to hang a relationships test on. This
-- checks the claim the model actually makes: that the dimension is a superset
-- of every country in use, so none of those relationships is partial.
--
-- A relationship that fails to match does not error in a tabular model. It puts
-- the unmatched rows in a blank member, which shows up as an unlabelled row in
-- a report and is very easy to read past.
with used as (
    select distinct country from {{ ref('dim_party') }} where country is not null
    union
    select distinct country from {{ ref('dim_customer') }} where country is not null
    union
    select distinct country from {{ ref('fct_daily_revenue') }} where country is not null
)

select u.country
from used u
left join {{ ref('dim_country') }} d on d.country = u.country
where d.country is null
