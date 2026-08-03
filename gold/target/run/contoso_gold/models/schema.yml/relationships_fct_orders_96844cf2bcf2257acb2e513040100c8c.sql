
    
    with test_main_sql as (
  
    
    
    

with child as (
    select customer_id as from_field
    from [aee86aac-de4c-435a-b206-1b1e54f4eb07].[dbo].[fct_orders]
    where customer_id is not null
),

parent as (
    select customer_id as to_field
    from [aee86aac-de4c-435a-b206-1b1e54f4eb07].[dbo].[dim_customer]
)

select
    from_field

from child
left join parent
    on child.from_field = parent.to_field

where parent.to_field is null



  
  ),
  dbt_internal_test as (
    select  * from test_main_sql
  )
  select
    count(*) as failures,
    case when count(*) != 0
      then 'true' else 'false' end as should_warn,
    case when count(*) != 0
      then 'true' else 'false' end as should_error
  from dbt_internal_test