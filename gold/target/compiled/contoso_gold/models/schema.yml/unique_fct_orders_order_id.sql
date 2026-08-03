
    
    

select
    order_id as unique_field,
    count(*) as n_records

from [aee86aac-de4c-435a-b206-1b1e54f4eb07].[dbo].[fct_orders]
where order_id is not null
group by order_id
having count(*) > 1


