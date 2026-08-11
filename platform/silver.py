"""Bronze → silver, as a Fabric NOTEBOOK.

The transform is `definitions/silver-conform.Notebook/notebook-content.py` and
it is not imported here — it is
published as the `notebook-content.py` of a Notebook item and executed by a
Spark engine. This module is the operator: publish, submit, wait, grade.

WHY THIS IS THE INTERESTING PART. Every other step in this platform reaches
Fabric's control plane and does the work itself. A notebook inverts that: the
platform hands Fabric some code and Fabric decides where it runs. That is how
the overwhelming majority of Fabric data engineering is actually written, and
until this step existed nothing here exercised it — the transform ran as a Spark
Connect script that merely *resembled* notebook code. `spark.py` has always
claimed "the same code is a Spark notebook or a Spark Job Definition in
production"; this is that claim under test rather than asserted.

WHAT DIFFERS BETWEEN THE TARGETS: nothing, now. Real Fabric runs the notebook on
its own Spark pool; the emulator drives the spark-agent that compose provides.
Either way this module submits and polls, which is the whole job. It was not
always so — the emulator's published agent image shipped empty, so this platform
had to execute the cells itself — and the absence of that code is the point.

Three rules the notebook implements, each because bronze deliberately violates
it: the vendor repeats rows, so customers are deduped on the key; orders arrive
at-least-once, so the LATEST event per order wins, ranked by the vendor's own
sequence; malformed orders are QUARANTINED, not dropped, because a row nobody
can price is still a row someone has to reconcile.
"""

from __future__ import annotations

import base64
import json
import pathlib
import time

import provision
import state
from fabric import FABRIC_AUD, await_operation, fabric, log, token

NOTEBOOK = "silver-conform"

# The notebook lives in Fabric's own SOURCE FORMAT: a `{display name}.{Type}/`
# directory holding the definition files plus `.platform`. That is exactly what
# Fabric's Git integration writes when a workspace is connected to Azure DevOps
# or GitHub, and what fabric-cicd deploys — so what this repository commits is
# what a real repository holds, rather than a loose .py the publisher happens to
# know the destination path of.
#
# `.platform` is not decoration: it carries the `logicalId`, the cross-workspace
# identity that survives a rename and a directory move. Publishing without one
# produces an item this emulator accepts (it stores parts verbatim) and that no
# CI/CD tool round-trips.
DEFINITION = (
    pathlib.Path(__file__).resolve().parent / "definitions" / f"{NOTEBOOK}.Notebook"
)
SOURCE = DEFINITION / "notebook-content.py"


def content(workspace: str, lakehouse: str) -> bytes:
    """The notebook's bytes, with its parameters cell filled in.

    Real Fabric would pass these through the job's `executionData.parameters`
    and leave the file untouched. The emulator implements no parameter
    override, so the ids are substituted before publishing — the one place this
    platform edits code rather than configuring it, and the reason the
    placeholders are shaped so that an unsubstituted notebook cannot silently
    resolve to somewhere real.
    """
    src = SOURCE.read_text(encoding="utf-8")
    src = src.replace("@@WORKSPACE@@", workspace).replace("@@LAKEHOUSE@@", lakehouse)
    assert "@@" not in src, "a placeholder survived substitution"
    return src.encode()


def publish(tok: str, workspace: str, body: bytes) -> str:
    """Create the Notebook item, or update the definition of the existing one.

    Resolve-or-create by NAME, like every other item in this platform: ids
    cannot match across targets, and a step that only works on a fresh
    workspace is not one anybody can operate.
    """
    # Every file in the definition directory becomes a part, keyed by its path
    # relative to that directory — the same mapping Git integration uses. The
    # notebook body is passed in because its parameters cell is substituted
    # first; everything else (`.platform`) ships as committed.
    parts = [
        {
            "path": "notebook-content.py",
            "payload": base64.b64encode(body).decode(),
            "payloadType": "InlineBase64",
        }
    ]
    for extra in sorted(DEFINITION.iterdir()):
        if extra.name == "notebook-content.py" or not extra.is_file():
            continue
        parts.append(
            {
                "path": extra.name,
                "payload": base64.b64encode(extra.read_bytes()).decode(),
                "payloadType": "InlineBase64",
            }
        )
    definition = {"parts": parts}

    found = provision.find_item(tok, workspace, NOTEBOOK, "Notebook")
    if found:
        r = fabric(
            "POST",
            f"/workspaces/{workspace}/items/{found['id']}/updateDefinition",
            tok,
            json={"definition": definition},
        )
        await_operation(r, tok, "updateDefinition")
        log(f"updated notebook {NOTEBOOK}")
        return found["id"]

    r = fabric(
        "POST",
        f"/workspaces/{workspace}/items",
        tok,
        json={"displayName": NOTEBOOK, "type": "Notebook", "definition": definition},
    )
    created = await_operation(r, tok, "create notebook")
    assert created.get("id"), created
    log(f"created notebook {NOTEBOOK}")
    return created["id"]


def await_job(tok: str, workspace: str, notebook: str, job: str) -> dict:
    """Poll the RunNotebook job to a terminal state, and return the run detail.

    THE STATUS IS EVIDENCE, not decoration. A RunNotebook job whose cells are
    still outstanding has no completion time at all, so reaching a terminal
    state here means an engine really executed and reported. It did not always:
    the job used to complete on a clock, reading `Completed` with every cell
    Pending and no engine having run a line.
    """
    base = f"/workspaces/{workspace}/items/{notebook}/jobs/instances/{job}"
    for _ in range(180):
        r = fabric("GET", base, tok)
        assert r.status_code == 200, (r.status_code, r.text[:200])
        status = r.json().get("status")
        if status in ("Completed", "Failed", "Cancelled", "Deduped"):
            assert status == "Completed", r.json()
            detail = fabric("GET", f"{base}/notebookRun", tok)
            assert detail.status_code == 200, (detail.status_code, detail.text[:200])
            return detail.json()
        time.sleep(1)
    raise SystemExit("the RunNotebook job never reached a terminal state")


def main() -> int:
    import reference_data as ref
    import source_system as src
    import web_store as web

    st = state.load()
    tok = token(FABRIC_AUD)
    ws, lake = st["workspace"], st["lakehouse"]

    notebook = publish(tok, ws, content(ws, lake))

    r = fabric(
        "POST",
        f"/workspaces/{ws}/items/{notebook}/jobs/instances?jobType=RunNotebook",
        tok,
    )
    assert r.status_code in (200, 202), (r.status_code, r.text[:300])
    job = r.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
    log(f"submitted RunNotebook job {job}")

    detail = await_job(tok, ws, notebook, job)
    assert detail.get("exitValue"), f"the notebook exited with no value: {detail}"
    got = json.loads(detail["exitValue"])

    # Graded against the GENERATOR, not against the run. The notebook reported
    # what it saw; source_system says what the vendor sent. A query grading its
    # own output confirms nothing.
    assert got["silver_customers"] == src.EXPECTED_SILVER_CUSTOMERS, got
    assert got["silver_orders"] == src.EXPECTED_SILVER_ORDERS, got
    assert got["silver_quarantine_orders"] == src.EXPECTED_QUARANTINED, got
    assert set(got["countries"]) == set(src.EXPECTED_COUNTRIES), got
    # Width, not just row count: gold's dimensions project from here, so a
    # narrow silver is a correctness failure every row count would pass over.
    assert got["customer_columns"] == src.EXPECTED_CUSTOMER_COLUMNS, got
    # The unmatchable cohort survives. It is the reason a resolution step that
    # claims 100% is lying, and dropping it here would erase the evidence.
    assert got["missing_email"] > 0, "the missing-email cohort vanished"

    # --- the reference feeds, and the rule applied to one of them -----------
    assert got["silver_product_hierarchy"] == ref.EXPECTED_PRODUCTS, got
    assert got["fx_currencies"] == ref.EXPECTED_FX_CURRENCIES, got

    # DENSE, which is the whole point: one row per currency per CALENDAR day,
    # where the vendor published one per currency per TRADING day. Deriving the
    # expected count here rather than trusting the notebook's own arithmetic —
    # a carry-forward that quietly produced the sparse table again would report
    # a consistent set of numbers and be wrong.
    assert got["silver_fx_daily"] == got["fx_currencies"] * got["fx_calendar_days"], got

    # Every dense row that is not one of the vendor's published rows was
    # carried, so the two must account for the table exactly. This is the
    # assertion that fails if the gaps stop being filled — or if they stop
    # existing, which would make the rule dead code while everything still
    # passed.
    assert got["fx_carried"] == got["silver_fx_daily"] - ref.EXPECTED_FX_ROWS, got
    assert got["fx_carried"] > 0, (
        "no FX rate was carried forward — the non-trading-day gaps are gone, "
        "so weekend revenue is no longer being converted by a stated rule"
    )

    # --- identity resolution ------------------------------------------------
    assert got["silver_web_customers"] == web.EXPECTED_WEB_CUSTOMERS, got

    # THE MATCH ITSELF, graded against the storefront's own contract rather
    # than against whatever the join produced.
    assert got["party_matched"] == web.EXPECTED_SHARED_EMAIL_COUNT, got
    assert got["party_web_only"] == web.EXPECTED_WEB_ONLY_EMAIL_COUNT, got

    # Every person is in exactly one cohort, and the cohorts account for the
    # whole party table. Without this the counts above could each be right
    # while the table held duplicates of the matched rows.
    assert (
        got["party_matched"] + got["party_pos_only"] + got["party_web_only"]
        == got["silver_party"]
    ), got

    # The POS customers whose email the vendor never sent SURVIVE, each as a
    # party of their own. They are unmatchable by construction, which is
    # exactly why they have to be visible: a resolution step that quietly
    # dropped them would report a higher match rate against a smaller
    # population and look better for it.
    assert got["party_no_email"] > 0, (
        "the customers with no email vanished from the party table — the "
        "cohort that can never be resolved is the one that proves the match "
        "rate is not being computed against a convenient subset"
    )

    # NORMALISING STRICTLY BEAT MATCHING RAW, and this is the assertion that
    # fails if the email conform is ever removed. 10% of POS emails carry mixed
    # case and none of the storefront's do, so a case-sensitive join finds
    # ~19.8k of the 22k people who are really in both systems. Every other
    # number here would still look healthy — there would simply be fewer
    # matches and more web-only shoppers, a shape indistinguishable from a
    # business whose customers genuinely do not overlap.
    assert got["naive_case_sensitive_matches"] < got["party_matched"], (
        f"conforming emails gained nothing: a case-sensitive match found "
        f"{got['naive_case_sensitive_matches']:,} and the conformed one "
        f"{got['party_matched']:,}. Either the normalisation was removed or "
        f"the vendor stopped sending mixed case — and the first is silent."
    )

    # THE OFFSETS WERE APPLIED. `placed_at` carries a real UTC offset on 15% of
    # orders, and the span only reaches back to 30 June once they are honoured
    # — which is a different FISCAL QUARTER from July. A reader that sliced the
    # first ten characters of the timestamp would produce a 1-28 July span and
    # file those orders in the wrong period, with nothing to show for it.
    lo, hi = got["web_order_date_span"]
    assert lo < "2026-07-01", (
        f"the storefront's orders start {lo}, so the UTC offsets were not "
        f"applied — 2,600 orders are filed under the shopper's local date"
    )
    assert hi > "2026-07-28", (lo, hi)

    state.save(
        silver={
            k: got[k]
            for k in (
                "silver_customers",
                "silver_orders",
                "silver_quarantine_orders",
                "silver_product_hierarchy",
                "silver_fx_daily",
                "silver_web_customers",
                "silver_web_order_lines",
                "silver_party",
            )
        },
        silver_notebook=notebook,
        silver_job=job,
    )
    log(
        f"silver: {got['silver_customers']:,} customers x "
        f"{got['customer_columns']} cols, {got['silver_orders']:,} orders, "
        f"{got['silver_quarantine_orders']:,} quarantined, "
        f"countries {got['countries']} — computed by a Fabric notebook; "
        f"FX densified to {got['silver_fx_daily']} rows over "
        f"{got['fx_calendar_days']} calendar days, {got['fx_carried']} carried"
    )
    log(
        f"identity: {got['silver_party']:,} parties — "
        f"{got['party_matched']:,} in both systems "
        f"(a case-sensitive match would have found "
        f"{got['naive_case_sensitive_matches']:,}), "
        f"{got['party_pos_only']:,} POS-only of which "
        f"{got['party_no_email']:,} have no email to match on, "
        f"{got['party_web_only']:,} web-only; "
        f"{got['silver_web_order_lines']:,} storefront lines spanning "
        f"{got['web_order_date_span'][0]}..{got['web_order_date_span'][1]} UTC"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
