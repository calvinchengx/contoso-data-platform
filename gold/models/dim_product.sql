-- The product dimension, and the rollup management reporting slices by.
--
-- SKU -> CATEGORY -> DEPARTMENT -> SEGMENT, and none of that hierarchy is
-- knowable from the selling systems. An order line names a `product_id` and
-- stops there; the POS export and the storefront both know a category, and
-- neither knows what a category rolls up to in the P&L. That mapping is the
-- group data office's, which is why Contoso Reference is a vendor rather than
-- a lookup table someone maintains inside this platform.
--
-- `segment` HERE IS A PRODUCT SEGMENT — "Core", "Peripheral". dim_customer
-- carries a `marketing_segment` that means something entirely different, and
-- both appear in fct_revenue_summary. The names are kept distinct rather than
-- both being called `segment`, because a report that joined the wrong one
-- would still produce a number.
--
-- IT ALSO CARRIES THE SKUs NOBODY PUBLISHED. The storefront ships 1,800 order
-- lines for products the group data office's catalogue does not contain — a
-- real condition, not a defect to filter. An inner join from the fact would
-- drop that revenue entirely; leaving it unmatched would fail the not-null
-- tests. So the unpublished ids become explicit members rolled up to
-- "Unallocated", which is what a P&L pack does with revenue it cannot attribute
-- yet: shows it on its own line and lets someone go and fix the master data.
with published as (
    select
        product_id,
        product_name,
        category,
        department,
        segment as product_segment,
        list_price_usd
    from {{ source('silver', 'silver_product_hierarchy') }}
),

-- Derived from the FACTS, so this can never list a phantom SKU that nothing
-- actually sold, and can never miss one that did.
unpublished as (
    select distinct
        s.product_id,
        'Unpublished SKU'          as product_name,
        'Unallocated'              as category,
        'Unallocated'              as department,
        'Unallocated'              as product_segment,
        -- DECIMAL, matching the published side. A `float` here would win the
        -- UNION and quietly demote every published price back to binary
        -- floating point — the column would still hold the right numbers and
        -- would no longer be money. Caught by reading the reflected type, not
        -- by any row being wrong.
        cast(null as decimal(19,4)) as list_price_usd
    from {{ ref('fct_sales') }} s
    where s.product_id not in (select product_id from published)
)

select * from published
union all
select * from unpublished
