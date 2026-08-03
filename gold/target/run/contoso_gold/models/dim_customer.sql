
  
    
    
    USE [aee86aac-de4c-435a-b206-1b1e54f4eb07];
    
    

    EXEC('create view [dbo].[dim_customer__dbt_temp__dbt_tmp_vw] as -- The customer dimension: a projection of silver, not a second source of truth.
select
    customer_id,
    name,
    email,
    country
from [a254f50a-6ef9-4312-82f4-1e31e2aae7d9].[dbo].[silver_customers];');




    
    

    
    
            EXEC('CREATE TABLE [aee86aac-de4c-435a-b206-1b1e54f4eb07].[dbo].[dim_customer__dbt_temp]  AS SELECT * FROM [aee86aac-de4c-435a-b206-1b1e54f4eb07].[dbo].[dim_customer__dbt_temp__dbt_tmp_vw] 
    OPTION (LABEL = ''dbt-fabric-dw'');
');
        
    


  
  