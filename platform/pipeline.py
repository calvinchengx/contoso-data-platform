"""Run the platform, in order, stopping at the first failure.

Steps are NAMED, not numbered: the order lives here and nowhere else, so
inserting one does not renumber a directory.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import capture

HERE = pathlib.Path(__file__).resolve().parent

STEPS = [
    ("provision", "workspace and lakehouse"),
    # Before anything that needs a credential. In production the vault is
    # already populated and this step does not exist.
    ("seed_secrets", "put the source credentials in Key Vault"),
    ("ingest_pos", "pull Contoso POS over HTTP into Files/landing"),
    # The connector goes BEFORE the replay. Start it after, and the history is
    # captured by a snapshot rather than as a change stream — which would still
    # produce rows, and might even match on count, while testing the wrong
    # thing entirely.
    ("erp_connector", "register Debezium against the ERP database"),
    ("erp_source", "seed Contoso ERP and replay its history as real DML"),
    ("ingest_erp_cdc", "consume the change stream into Files/landing"),
    ("bronze", "landing -> bronze Delta tables, verbatim"),
    ("silver", "bronze -> silver: dedupe, conform, quarantine"),
    ("gold", "silver -> gold: the star, in the Warehouse via dbt-fabric"),
    ("semantic_model", "publish the model; query it with DAX over executeQueries"),
    ("xmla_probe", "run the same DAX through a real BI client over XMLA"),
    # In the run, not beside it. A catalog exercised only by a separate command
    # is a catalog nobody finds out about when it breaks — and this one carries
    # the platform's headline claim, that lineage reaches the vendor.
    ("govern", "catalog the platform in OpenMetadata, sources included"),
]


def preflight() -> None:
    """Fail with the fix, not with an ImportError six steps in.

    The generators live outside the lock on purpose, so any `uv sync` prunes
    them. A step that dies on `ModuleNotFoundError: source_system` sends the
    reader to look for a missing file rather than to `make fixtures`.
    """
    try:
        import erp_system  # noqa: F401
        import source_system  # noqa: F401
        import web_store  # noqa: F401
    except ModuleNotFoundError as exc:
        raise SystemExit(
            f"the fixture generators are not installed ({exc.name}).\n"
            f"  run `make fixtures` — and note that any `uv sync` prunes them, "
            f"because they are pinned to a release rather than to uv.lock."
        ) from exc

    # The vendors serve FILES. Without them mokapi does not fail — it falls
    # back to generating bodies from the OpenAPI schema and answers everything
    # 200, wrong API key included. That surfaced once as "the vendor accepted a
    # bad API key", an error naming neither mokapi nor the absent files. The
    # compose healthcheck catches it too; this catches it before the stack is
    # even up, and says which command fixes it.
    for feed in ("customers", "orders"):
        pages = HERE.parent / "sources" / "_data" / "contoso-pos" / feed / "pages.txt"
        if not pages.exists():
            raise SystemExit(
                f"the vendor's exports are not materialised ({feed}).\n"
                f"  run `make sources` — sources/_data/ is gitignored, so a "
                f"fresh clone has nothing for the source APIs to serve."
            )


def main() -> int:
    preflight()

    # Record the flow view for the WHOLE run. Started here because this is the
    # only place that knows when the run begins; a capture script invoked as a
    # step could only ever film what came after it.
    watcher = None
    if os.environ.get("CAPTURE", "1") != "0":
        try:
            watcher = capture.start_watch()
        except Exception as exc:
            print(f"==> capture unavailable, continuing without it: {exc}", flush=True)

    for i, (step, title) in enumerate(STEPS, 1):
        print(f"==> [{i}/{len(STEPS)}] {title}", flush=True)
        rc = subprocess.run([sys.executable, f"{step}.py"], cwd=HERE).returncode
        if rc != 0:
            if watcher:
                capture.stop_watch(watcher)
            return rc or 1
    if watcher:
        capture.stop_watch(watcher)
        # Verified and photographed only after everything is built — the
        # catalog is a result, so there is nothing true to shoot before now.
        capture.main()

    print(f"==> platform complete: {len(STEPS)}/{len(STEPS)} steps passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
