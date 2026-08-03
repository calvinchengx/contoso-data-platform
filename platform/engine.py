"""The Spark pool, when the target does not bring its own.

**This module never runs against real Fabric.** Submitting a RunNotebook job to
Fabric is the whole of the client's work: Fabric schedules the notebook onto a
Spark pool, the pool executes it and reports back, and the caller polls. The
emulator deliberately stops one step short — it parses the Notebook item into
ordered cells and records a `Pending` run, then waits for an engine to execute
them and report the outcome.

That is not a gap; it is the emulator refusing to lie. A RunNotebook job used to
reach `Completed` on a clock, with every cell still Pending and no engine having
run a line, and callers reasonably read that as "the notebook ran". Now only an
engine's report finishes the run, so a terminal status means execution happened.

Which leaves the platform to play the pool locally. That role is scaffolding, it
exists only because of the emulator, and `T.runs_notebooks_itself` is what keeps
it off the production path — see RULES.md rule 1.
"""

from __future__ import annotations

import io
import time
from contextlib import redirect_stdout
from typing import Any

from fabric import FABRIC_AUD, T, fabric, log, token


def _session():
    """Connect to the Spark Connect endpoint, tolerating a slow start.

    Imported here rather than at module scope because this module is only
    reached on the emulator path, and a platform running against real Fabric
    should not need a Connect client at all.
    """
    from pyspark.sql import SparkSession

    assert T.spark_remote, (
        "no SPARK_REMOTE — the emulator does not execute notebooks, so the "
        "platform must attach an engine to run one"
    )
    last: Exception | None = None
    for _ in range(30):
        try:
            spark = SparkSession.builder.remote(T.spark_remote).getOrCreate()
            spark.sql("SELECT 1").collect()
            return spark
        except Exception as exc:  # the engine may still be binding its port
            last = exc
            time.sleep(2)
    raise SystemExit(f"Spark at {T.spark_remote} never became ready: {last}")


def run(workspace: str, notebook: str, job: str, item: str) -> str:
    """Execute the cells the emulator parsed and report the run. Returns the
    notebook's exit value.

    Lineage is reported PER CELL, from what the notebook's own IO helpers
    recorded while that cell ran (its `LINEAGE` list). The emulator records
    edges from what an engine reports and never parses user code to guess, so
    the reporting has to be as precise as the truth is.

    It was not, at first: the publishing step declared one read set and one
    write set for the whole notebook, and the emulator — correctly — paired
    every read with every write. Silver reads two tables and writes three, so
    six edges appeared where three movements had happened, and half the graph
    described data that never moved.
    """
    tok = token(FABRIC_AUD)
    base = f"/workspaces/{workspace}/items/{notebook}/jobs/instances/{job}"

    # The cells the EMULATOR parsed, not the source that was uploaded. What an
    # engine is asked to execute is the run, and a mismatch between the two is
    # precisely the kind of thing this exercise exists to catch.
    r = fabric("GET", f"{base}/notebookRun", tok)
    assert r.status_code == 200, (r.status_code, r.text[:300])
    cells = sorted(r.json()["cells"], key=lambda c: c["index"])
    assert cells, "the emulator parsed no cells from the notebook"

    spark = _session()
    log(f"engine: executing {len(cells)} parsed cell(s) on {T.spark_remote}")

    exit_value = ""

    def notebook_exit(value: str = "") -> None:
        nonlocal exit_value
        exit_value = str(value)

    # ONE namespace across every cell. A notebook is not a sequence of scripts —
    # cell 3 uses the DataFrame cell 2 named — and running each in a fresh
    # namespace would pass for a one-cell notebook and fail for any real one.
    # `Any`, because an exec namespace genuinely holds arbitrary objects: a
    # SparkSession, a callback, and whatever the notebook itself binds.
    ns: dict[str, Any] = {
        "spark": spark,
        "notebook_exit": notebook_exit,
        "__name__": "__nb__",
    }

    results, overall = [], "Completed"
    # How much of the notebook's movement log has already been attributed. The
    # delta after each cell is what THAT cell did.
    seen = 0
    for cell in cells:
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                exec(cell["source"], ns)  # running notebook cells IS the engine's job
            result = {
                "index": cell["index"],
                "status": "Succeeded",
                "output": buf.getvalue(),
            }
            # A notebook that records nothing moved nothing as far as anyone
            # can tell, and reporting a guess instead is the whole failure this
            # attribution exists to avoid.
            recorded = ns.get("LINEAGE")
            moved = recorded[seen:] if isinstance(recorded, list) else []
            seen += len(moved)
            reads = [{"itemId": item, "path": p} for kind, p in moved if kind == "read"]
            writes = [
                {"itemId": item, "path": p} for kind, p in moved if kind == "write"
            ]
            # Only when the cell actually moved something. A cell that reads
            # without writing has no edge to contribute, and saying so with an
            # empty list is not the same as saying nothing.
            if reads and writes:
                result["reads"] = reads
                result["writes"] = writes
            results.append(result)
        except Exception as exc:  # a failed cell is a reported result, not a crash
            results.append(
                {"index": cell["index"], "status": "Failed", "error": str(exc)}
            )
            overall = "Failed"
            # Stop at the first failure, as Fabric does: later cells depend on
            # the namespace this one did not finish building.
            break

    # A failed run reports the movements of the cells that DID complete and
    # nothing more: the cell that raised may have written half a table, and
    # claiming the edge would put a movement in the graph that did not finish.
    if overall == "Failed":
        for c in results:
            if c["status"] != "Succeeded":
                c.pop("reads", None)
                c.pop("writes", None)

    r = fabric(
        "POST",
        f"{base}/notebookRunResult",
        tok,
        json={"status": overall, "exitValue": exit_value, "cells": results},
    )
    assert r.status_code == 200, (r.status_code, r.text[:300])

    failed = [c for c in results if c["status"] == "Failed"]
    assert overall == "Completed", f"the notebook failed on Spark: {failed}"
    log(f"engine: {len(results)} cell(s) succeeded, run reported with its lineage")
    return exit_value
