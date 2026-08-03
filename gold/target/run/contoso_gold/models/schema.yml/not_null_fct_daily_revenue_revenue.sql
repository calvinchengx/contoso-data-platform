
    
    with test_main_sql as (
  
    
    
    



select revenue
from [aee86aac-de4c-435a-b206-1b1e54f4eb07].[dbo].[fct_daily_revenue]
where revenue is null



  
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