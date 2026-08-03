
  
    
    
    USE [aee86aac-de4c-435a-b206-1b1e54f4eb07];
    
    

    EXEC('create view [dbo].[fct_daily_revenue__dbt_temp__dbt_tmp_vw] as -- The reporting aggregate the semantic model serves.
select
    o.order_date,
    c.country,
    count(*)        as orders,
    sum(o.quantity) as units,
    sum(o.amount)   as revenue
from [aee86aac-de4c-435a-b206-1b1e54f4eb07].[dbo].[fct_orders] o
join [aee86aac-de4c-435a-b206-1b1e54f4eb07].[dbo].[dim_customer] c
  on o.customer_id = c.customer_id
group by o.order_date, c.country;');




    
    

    
    
            EXEC('CREATE TABLE [aee86aac-de4c-435a-b206-1b1e54f4eb07].[dbo].[fct_daily_revenue__dbt_temp]  AS SELECT * FROM [aee86aac-de4c-435a-b206-1b1e54f4eb07].[dbo].[fct_daily_revenue__dbt_temp__dbt_tmp_vw] 
    OPTION (LABEL = ''dbt-fabric-dw'');
');
        
    


  
  