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
select
    product_id,
    product_name,
    category,
    department,
    segment as product_segment,
    list_price_usd
from {{ source('silver', 'silver_product_hierarchy') }}
