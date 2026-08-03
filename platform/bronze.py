"""Landing → bronze: the bytes as they arrived, as Delta tables.

Bronze parses and nothing more. No dedupe, no conforming, no quarantine —
those are silver's job, and doing them here would destroy the only copy of what
the vendor actually sent. The redeliveries POS emits and the change events ERP
produced are all still here, which is what makes a question about the source
answerable without going back to the vendor.
"""

from __future__ import annotations

import io

import onelake_delta as delta
import pyarrow.csv as pacsv
import pyarrow.json as pajson
import pyarrow.parquet as pq
import state
from fabric import STORAGE_AUD, log, onelake, token


def fetch(st: dict, rel: str, tok: str) -> bytes:
    r = onelake("GET", f"/{st['workspace']}/{st['lakehouse']}/{rel}", tok)
    assert r.status_code == 200, (rel, r.status_code, r.text[:200])
    return r.content


def main() -> int:
    import erp_system as erp
    import source_system as src

    st = state.load()
    tok = token(STORAGE_AUD)
    day = st["landing_day"]

    # --- Contoso POS -------------------------------------------------------
    csv_bytes = fetch(st, f"Files/landing/contoso_pos/{day}/customers.csv", tok)
    # Every column as string: the vendor's CSV is text on the wire, and letting
    # a parser guess types here would silently make bronze an interpretation
    # rather than a copy.
    customers = pacsv.read_csv(
        io.BytesIO(csv_bytes),
        convert_options=pacsv.ConvertOptions(strings_can_be_null=False),
        parse_options=pacsv.ParseOptions(newlines_in_values=True),
    )
    n_cust = delta.write(
        st["workspace"], st["lakehouse"], "bronze_customers", customers, tok
    )

    jsonl = fetch(st, f"Files/landing/contoso_pos/{day}/orders.jsonl", tok)
    orders = pajson.read_json(io.BytesIO(jsonl))
    n_ord = delta.write(st["workspace"], st["lakehouse"], "bronze_orders", orders, tok)

    # --- Contoso ERP -------------------------------------------------------
    erp_bytes = fetch(st, f"Files/landing/contoso_erp/{day}/changes.parquet", tok)
    changes = pq.read_table(io.BytesIO(erp_bytes))
    n_erp = delta.write(
        st["workspace"], st["lakehouse"], "bronze_erp_changes", changes, tok
    )

    # --- what bronze must have preserved -----------------------------------
    # The vendor repeats a share of its rows. Bronze holding MORE rows than
    # distinct customers is the property silver's dedupe exists to fix — and if
    # bronze had already deduped, silver would pass its own assertions while
    # testing nothing.
    # `.unique()` rather than pyarrow.compute.count_distinct: compute
    # generates its functions at import time, so a type checker cannot see
    # them, and a suppression here would be hiding a real blind spot rather
    # than a false positive.
    distinct_cust = len(customers["customer_id"].unique())
    assert distinct_cust == src.EXPECTED_SILVER_CUSTOMERS, (
        distinct_cust,
        src.EXPECTED_SILVER_CUSTOMERS,
    )
    assert n_cust > distinct_cust, (
        f"bronze holds {n_cust:,} rows for {distinct_cust:,} customers — the "
        f"vendor's redeliveries are missing, so silver's dedupe has nothing to do"
    )
    assert customers.num_columns == src.EXPECTED_CUSTOMER_COLUMNS, (
        customers.num_columns,
        src.EXPECTED_CUSTOMER_COLUMNS,
    )

    # Orders arrive at-least-once, so bronze must exceed the distinct order
    # count that silver settles on.
    distinct_ord = len(orders["order_id"].unique())
    assert n_ord > distinct_ord, (n_ord, distinct_ord)

    assert n_erp == erp.EXPECTED_ERP_CHANGE_EVENTS, (
        n_erp,
        erp.EXPECTED_ERP_CHANGE_EVENTS,
    )

    state.save(
        bronze={
            "bronze_customers": n_cust,
            "bronze_orders": n_ord,
            "bronze_erp_changes": n_erp,
        }
    )
    log(
        f"bronze: {n_cust:,} customer rows ({distinct_cust:,} distinct, "
        f"{customers.num_columns} cols), {n_ord:,} order events "
        f"({distinct_ord:,} distinct), {n_erp:,} ERP change events"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
