-- Both businesses must appear in the reporting pack, and the storefront must
-- bring its whole self with it.
--
-- WHY THIS IS NOT PARANOIA. The storefront reaches the pack through two joins
-- that can each fail quietly: the resolved party key, and the product rollup
-- that does not publish every SKU it sells. Either one silently dropping web
-- rows leaves a pack that still totals correctly and simply describes a
-- business with no online channel — which is indistinguishable from a company
-- that has none, unless something checks.
--
-- Returns a row (failing) if either system is missing, if cancellations
-- vanished, or if the unpublished-SKU revenue stopped being reported.
select 'no WEB revenue' as problem
from {{ ref('fct_revenue_summary') }}
having sum(case when channel_system = 'WEB' then revenue_usd else 0 end) = 0

union all
select 'no POS revenue'
from {{ ref('fct_revenue_summary') }}
having sum(case when channel_system = 'POS' then revenue_usd else 0 end) = 0

union all
-- Only the storefront cancels, so this also proves web rows carry their status.
select 'no cancelled revenue'
from {{ ref('fct_revenue_summary') }}
having sum(cancelled_revenue_usd) = 0

union all
-- The SKUs the group data office never published still have to be reported
-- rather than dropped by the product join.
select 'unallocated product revenue vanished'
from {{ ref('fct_revenue_summary') }}
having sum(case when department = 'Unallocated' then revenue_usd else 0 end) = 0

union all
-- The web-only shoppers, who have no POS segment, must survive the party join.
select 'unsegmented customer revenue vanished'
from {{ ref('fct_revenue_summary') }}
having sum(case when customer_segment = 'Unsegmented' then revenue_usd else 0 end) = 0

union all
-- The fiscal calendar must span more than one quarter, which it only does
-- because the storefront's UTC offsets pull sales back into 30 June.
select 'the pack covers only one fiscal quarter'
from {{ ref('fct_revenue_summary') }}
having count(distinct fiscal_quarter_label) < 2
