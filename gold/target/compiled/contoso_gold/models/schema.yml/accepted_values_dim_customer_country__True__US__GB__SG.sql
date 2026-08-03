
    
    

with all_values as (

    select
        country as value_field,
        count(*) as n_records

    from [aee86aac-de4c-435a-b206-1b1e54f4eb07].[dbo].[dim_customer]
    group by country

)

select *
from all_values
where value_field not in (
    'US','GB','SG'
)


