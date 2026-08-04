-- Contoso's financial year starts 1 APRIL, so it must not agree with the
-- calendar year for any date from April onwards.
--
-- WHY THIS IS WORTH A TEST. A dim_date whose fiscal columns silently became
-- copies of the calendar ones would pass every other check in this project:
-- the keys stay unique, the quarters stay within 1-4, the joins still match,
-- and the pack still totals correctly. It would simply be reporting the wrong
-- year — which for a business whose FY ends 31 March is wrong for nine months
-- of every twelve.
--
-- Today every order falls in July 2026, which must report as FY27.
select
    date_key,
    calendar_year,
    fiscal_year
from {{ ref('dim_date') }}
where calendar_month >= 4
  and fiscal_year <> calendar_year + 1
