#!/usr/bin/env python3
"""Read every event a run emits, and say what was unexpected.

WHY THIS IS NOT THE VIDEO. A recording shows a graph filling in. It cannot show
a job that failed and was retried past, or a batch of events dropped — which
means the graph a viewer watched was INCOMPLETE and looked no different. Neither
appears in `14/14 steps passed` either.

WHY THE RULES ARE PINNED TO THE EMULATOR'S OWN VOCABULARY. The first draft of
this flagged every `job` event whose status was not in
`("", "NotStarted", "InProgress", "Completed")` — but the bus publishes
`JobStarted = "Started"` when a job begins, a value that appears in no status
enum. Every job start would have been reported as suspicious, and real failures
would have been buried in the noise. The constants below are read off
`internal/store/jobs.go` and `internal/store/lineage.go`, and
`test_the_observer_matches_the_emulators_vocabulary` fails if this drifts.

A signal that cannot fire is worse than no signal, because it reads as an
all-clear. The first draft also flagged `lineage` events with no producer —
`CreateLineageEdge` defaults an empty producer to `Copy` before publishing, so
that rule could never have fired once. What replaces it is the distinction that
actually matters: whether the graph is EVIDENCE or a CLAIM.
"""

from __future__ import annotations

import collections
import json
import pathlib
import ssl
import sys
import time
import urllib.request

# --- the emulator's vocabulary, not ours -----------------------------------
# internal/store/bus.go
KIND_DROPPED = "dropped"
KIND_JOB = "job"
KIND_ACTIVITY = "activity"
KIND_LINEAGE = "lineage"

# internal/store/jobs.go, plus JobStarted from bus.go — which is NOT in the
# status enum and is exactly what the first draft of this got wrong.
JOB_STARTED = "Started"
JOB_NOT_STARTED = "NotStarted"
JOB_IN_PROGRESS = "InProgress"
JOB_COMPLETED = "Completed"
JOB_FAILED = "Failed"
JOB_CANCELLED = "Cancelled"
JOB_FINE = frozenset({JOB_STARTED, JOB_NOT_STARTED, JOB_IN_PROGRESS, JOB_COMPLETED})

# internal/store/lineage.go. The split is the point: a producer is either
# something the emulator WATCHED or something a client TOLD it.
PRODUCERS_OBSERVED = frozenset({"Copy", "NotebookObserved", "Warehouse", "DirectLake"})
PRODUCERS_CLAIMED = frozenset({"Notebook", "Reported"})
PRODUCERS = PRODUCERS_OBSERVED | PRODUCERS_CLAIMED


def classify(event: dict) -> tuple[str, str] | None:
    """Return (tag, why) when an event is worth a human's attention.

    Pure, so the rules can be tested without a stack. Returning None is the
    common case and means nothing about the event was surprising.
    """
    kind = event.get("kind")
    status = event.get("status") or ""

    if kind == KIND_DROPPED:
        n = event.get("dropped", 0)
        return ("DROPPED", f"{n} event(s) lost — a viewer's graph was incomplete")

    if kind == KIND_JOB and status and status not in JOB_FINE:
        why = event.get("failureReason") or ""
        return ("JOB", f"{event.get('jobType', '?')} -> {status} {why}".strip())

    if kind == KIND_ACTIVITY:
        if event.get("error") or status == JOB_FAILED:
            detail = event.get("error") or status
            return ("ACTIVITY", f"{event.get('activityName', '?')}: {detail}")
        if event.get("retryAttempt"):
            return (
                "RETRY",
                f"{event.get('activityName', '?')} succeeded on "
                f"attempt {event['retryAttempt']}",
            )

    if kind == KIND_LINEAGE:
        producer = event.get("producer") or ""
        # An empty producer cannot occur — CreateLineageEdge defaults it — so
        # checking for one would be an all-clear that means nothing. An
        # UNKNOWN producer can occur, if the emulator grows one this has not
        # been taught, and that is worth saying rather than silently trusting.
        if producer not in PRODUCERS:
            return (
                "LINEAGE",
                f"unknown producer {producer!r} — this observer "
                f"cannot say if it is evidence or a claim",
            )

    return None


def summarise(events: list[dict]) -> dict:
    """Counts and the one judgement a per-event rule cannot make.

    Whether the graph is entirely self-reported is a property of the WHOLE run:
    every individual `Reported` edge is legitimate, and a run with nothing but
    them is a graph the emulator never witnessed.
    """
    kinds = collections.Counter(e.get("kind", "?") for e in events)
    producers = collections.Counter(
        e.get("producer", "") for e in events if e.get("kind") == KIND_LINEAGE
    )
    observed = sum(n for p, n in producers.items() if p in PRODUCERS_OBSERVED)
    claimed = sum(n for p, n in producers.items() if p in PRODUCERS_CLAIMED)
    return {
        "kinds": dict(kinds),
        "producers": dict(producers),
        "observed": observed,
        "claimed": claimed,
        # Only when there IS lineage: a run with no edges at all is a different
        # problem, and reporting it here would mislabel it.
        "all_claimed": bool(producers) and observed == 0,
    }


def stream(portal: str, out_path: pathlib.Path) -> list[dict]:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # the stack's self-signed dev certificate

    events: list[dict] = []
    started = time.time()
    print(f"observing {portal}/_emulator/events", flush=True)
    try:
        req = urllib.request.Request(f"{portal}/_emulator/events")
        with urllib.request.urlopen(req, context=ctx) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    events.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    continue
                if len(events) % 250 == 0:
                    print(f"  {len(events)} events", flush=True)
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # the stream ends when the stack goes down
        print(f"stream ended: {type(exc).__name__}: {exc}", flush=True)

    out_path.write_text("\n".join(json.dumps(e) for e in events))
    elapsed = time.time() - started
    print(f"{len(events)} events in {elapsed:.0f}s -> {out_path}", flush=True)
    return events


def report(events: list[dict]) -> int:
    s = summarise(events)
    print(f"\n=== {len(events)} events ===")
    for k, n in sorted(s["kinds"].items(), key=lambda kv: -kv[1]):
        print(f"  {k:10s} {n:6d}")
    if s["producers"]:
        print(f"\n=== lineage: {s['observed']} observed, {s['claimed']} claimed ===")
        for p, n in sorted(s["producers"].items(), key=lambda kv: -kv[1]):
            mark = "evidence" if p in PRODUCERS_OBSERVED else "claim"
            print(f"  {p:18s} {n:4d}  ({mark})")

    findings = [f for f in (classify(e) for e in events) if f]
    if s["all_claimed"]:
        findings.append(
            (
                "GRAPH",
                "every lineage edge is a CLAIM — the emulator "
                "witnessed none of this run's movements",
            )
        )

    print(f"\n=== {len(findings)} finding(s) ===")
    for tag, why in findings[:60]:
        print(f"  [{tag}] {why}")
    if not findings:
        print("  (none)")
    return 0


def main() -> int:
    portal = sys.argv[1] if len(sys.argv) > 1 else "https://localhost:9443"
    default_out = "capture/shots/events.jsonl"
    out = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else default_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    return report(stream(portal, out))


if __name__ == "__main__":
    raise SystemExit(main())
