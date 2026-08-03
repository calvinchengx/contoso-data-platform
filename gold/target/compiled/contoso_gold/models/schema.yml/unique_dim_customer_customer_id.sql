
    
    

select
    customer_id as unique_field,
    count(*) as n_records

from [aee86aac-de4c-435a-b206-1b1e54f4eb07].[dbo].[dim_customer]
where customer_id is not null
group by customer_id
having count(*) > 1


