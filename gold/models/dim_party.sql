-- The PERSON dimension, across both selling systems.
--
-- WHY THIS EXISTS ALONGSIDE dim_customer. `dim_customer` is the POS system's
-- customer, keyed on its `customer_id`, and it is still correct for anything
-- that only concerns the shops. It cannot carry the storefront: Contoso Web has
-- no customer id at all, so there is no key to extend. `party_key` is the
-- resolved identity — the normalised email where a person has one, and the POS
-- id where they do not — computed once in silver.
--
-- THREE COHORTS, and all three are here on purpose:
--
--   in_pos AND in_web    22,000 people both systems know, and neither system
--                        knows it. This is the join the platform exists to make.
--   in_pos only          78,000, of whom 3,079 have no email the vendor ever
--                        sent. Those cannot be matched by construction, and
--                        they are kept rather than dropped — a match rate
--                        computed after quietly deleting the unmatchable is a
--                        match rate against a convenient population.
--   in_web only          18,000 shoppers who have never been in a shop.
--
-- SEGMENT AND TIER ARE NULL FOR WEB-ONLY SHOPPERS, and left that way. Only the
-- POS system segments its customers. Bucketing the rest as "unknown" would put
-- invented rows in a P&L pack; a NULL says the business does not know, which
-- is the truth and is reportable as its own line.
select
    party_key,
    email,
    pos_customer_id,
    in_pos,
    in_web,
    country,
    marketing_segment,
    loyalty_tier
from {{ source('silver', 'silver_party') }}
