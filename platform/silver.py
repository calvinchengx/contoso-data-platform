"""Bronze → silver: dedupe, conform, quarantine.

Three rules, and each exists because bronze deliberately violates it:

  * the vendor repeats rows, so customers are deduped on the key
  * orders arrive at-least-once, so the LATEST event per order wins — ranked by
    the vendor's own sequence, never "pick any", which is correct only by luck
    and silently wrong the day a redelivery carries a different status
  * malformed orders are QUARANTINED, not dropped: a row nobody can price is
    still a row someone has to reconcile

Expressed as SQL over Arrow with DuckDB, in-process. Silver is set logic, and
`row_number() over (partition by ...)` says what it means; the equivalent chain
of Arrow compute calls would not. No engine is added to the stack for it.
"""

from __future__ import annotations

import duckdb
import onelake_delta as delta
import state
from fabric import STORAGE_AUD, log, token

# Silver's own business rule, written out rather than derived from the
# generator's COUNTRY_VARIANTS. Importing that mapping would make the
# conformance assertion agree with itself, and a new variant appearing upstream
# would silently conform instead of failing.
CONFORM_COUNTRY = """
    case upper(trim(country))
        when 'US' then 'US' when 'USA' then 'US' when 'U.S.' then 'US'
        when 'UNITED STATES' then 'US'
        when 'GB' then 'GB' when 'GBR' then 'GB' when 'UK' then 'GB'
        when 'U.K.' then 'GB' when 'UNITED KINGDOM' then 'GB'
        when 'SG' then 'SG' when 'SGP' then 'SG' when 'SINGAPORE' then 'SG'
        else upper(trim(country))
    end
"""


def main() -> int:
    import source_system as src

    st = state.load()
    tok = token(STORAGE_AUD)
    ws, lake = st["workspace"], st["lakehouse"]

    bronze_customers = delta.read(ws, lake, "bronze_customers", tok)  # noqa: F841
    bronze_orders = delta.read(ws, lake, "bronze_orders", tok)  # noqa: F841

    con = duckdb.connect()

    # WIDE, deliberately. Silver is the conformed customer-360 and gold's
    # dimensions are a projection of it — not the other way round. `* EXCLUDE`
    # keeps all ~100 source columns while replacing the two silver reasons
    # about, so a narrow silver cannot happen by omission.
    customers = con.execute(f"""
        select * exclude (_rn)
        from (
            -- EXCLUDE then re-add, in the INNER query. Adding a computed
            -- `email` beside the source `email` leaves two columns of that
            -- name, and the outer projection then picks the first — the RAW
            -- one. Silver looked conformed, asserted conformed, and shipped
            -- 'U.S.' and 'singapore' straight through.
            select * exclude (email, country),
                   lower(trim(coalesce(email, ''))) as email,
                   {CONFORM_COUNTRY} as country,
                   row_number() over (partition by customer_id) as _rn
            from bronze_customers
        )
        where _rn = 1
    """).to_arrow_table()

    # Latest event wins, by the VENDOR's sequence. `qualify` filters on the
    # window without a subquery, and ordering by event_seq rather than by
    # arrival is the whole point: the two disagree.
    clean = con.execute("""
        select * exclude (_rn, quantity, unit_price),
               quantity, unit_price,
               quantity * unit_price as amount
        from (
            select *, row_number() over (
                       partition by order_id order by event_seq desc
                     ) as _rn
            from bronze_orders
        )
        -- The two rules that decide clean from quarantined. Types come from
        -- bronze's JSON Lines: quantity is int64 and unit_price is double with
        -- real nulls, so these are value tests, not string tests.
        where _rn = 1
          and quantity > 0
          and unit_price is not null
    """).to_arrow_table()

    quarantine = con.execute("""
        select * exclude (_rn)
        from (
            select *, row_number() over (
                       partition by order_id order by event_seq desc
                     ) as _rn
            from bronze_orders
        )
        where _rn = 1
          and (quantity <= 0 or unit_price is null)
    """).to_arrow_table()

    n_cust = delta.write(ws, lake, "silver_customers", customers, tok)
    n_ord = delta.write(ws, lake, "silver_orders", clean, tok)
    n_quar = delta.write(ws, lake, "silver_quarantine_orders", quarantine, tok)

    countries = {
        r[0] for r in con.execute("select distinct country from customers").fetchall()
    }

    assert n_cust == src.EXPECTED_SILVER_CUSTOMERS, (
        n_cust,
        src.EXPECTED_SILVER_CUSTOMERS,
    )
    assert n_ord == src.EXPECTED_SILVER_ORDERS, (n_ord, src.EXPECTED_SILVER_ORDERS)
    assert n_quar == src.EXPECTED_QUARANTINED, (n_quar, src.EXPECTED_QUARANTINED)
    assert countries == src.EXPECTED_COUNTRIES, (
        sorted(countries),
        src.EXPECTED_COUNTRIES,
    )
    # Width, not just row count: gold's dimensions project from here, so a
    # narrow silver is a correctness failure every row count would pass over.
    assert customers.num_columns == src.EXPECTED_CUSTOMER_COLUMNS, (
        customers.num_columns,
        src.EXPECTED_CUSTOMER_COLUMNS,
    )
    # The unmatchable cohort survives. It is the reason a resolution step that
    # claims 100% is lying, and dropping it here would erase the evidence.
    missing_email = con.execute(
        "select count(*) from customers where email = ''"
    ).fetchone()
    assert missing_email and missing_email[0] > 0, "the missing-email cohort vanished"

    state.save(
        silver={
            "silver_customers": n_cust,
            "silver_orders": n_ord,
            "silver_quarantine_orders": n_quar,
        }
    )
    log(
        f"silver: {n_cust:,} customers x {customers.num_columns} cols, "
        f"{n_ord:,} orders, {n_quar:,} quarantined, countries {sorted(countries)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
