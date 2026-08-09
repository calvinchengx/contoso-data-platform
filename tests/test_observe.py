"""Every signal the observer claims to detect, exercised.

An observer that reports nothing is indistinguishable from a clean run, so a
rule that cannot fire is not a gap — it is a false all-clear. Two of the five
original rules were exactly that:

  * `job` flagged every status outside ("", NotStarted, InProgress, Completed),
    but the bus publishes `Started` when a job begins. Every job start would
    have been reported, burying real failures in noise.
  * `lineage` flagged edges with no producer, which `CreateLineageEdge` makes
    impossible by defaulting an empty producer to `Copy` before publishing.

Both were found by reading the emulator rather than by running this, which is
why `test_the_observer_matches_the_emulators_vocabulary` exists: it fails when
the emulator's constants move, instead of leaving this quietly wrong.
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from observe import (  # noqa: E402
    JOB_FINE,
    PRODUCERS,
    PRODUCERS_CLAIMED,
    PRODUCERS_OBSERVED,
    classify,
    summarise,
)


def ev(**kw):
    return {"seq": 1, "at": 0, **kw}


class TestDropped:
    """`dropped` is real: internal/server/events.go synthesises it from
    Subscription.TakeDropped when a subscriber falls behind."""

    def test_a_drop_is_reported_with_its_count(self):
        got = classify(ev(kind="dropped", dropped=17))
        assert got and got[0] == "DROPPED"
        assert "17" in got[1]

    def test_the_reason_it_matters_is_stated(self):
        # A viewer cannot tell a graph with missing nodes from a correct one.
        _, why = classify(ev(kind="dropped", dropped=1))
        assert "incomplete" in why


class TestJobStatus:
    @pytest.mark.parametrize("status", sorted(JOB_FINE))
    def test_normal_statuses_are_silent(self, status):
        # "Started" is the one that matters here: it is published by the bus,
        # is absent from the status enum, and flagging it made every job in a
        # run look suspicious.
        assert classify(ev(kind="job", status=status, jobType="RunNotebook")) is None

    def test_failed_is_reported_with_its_reason(self):
        got = classify(
            ev(
                kind="job",
                status="Failed",
                jobType="RunNotebook",
                failureReason="NotebookExecutionFailed",
            )
        )
        assert got and got[0] == "JOB"
        assert "NotebookExecutionFailed" in got[1]

    def test_cancelled_is_reported(self):
        got = classify(ev(kind="job", status="Cancelled", jobType="Refresh"))
        assert got and got[0] == "JOB"

    def test_an_empty_status_is_not_a_failure(self):
        # Some job events carry no status; absent is not the same as bad.
        assert classify(ev(kind="job", jobType="Refresh")) is None


class TestActivity:
    def test_an_error_is_reported_with_the_activity_name(self):
        got = classify(
            ev(kind="activity", activityName="CopyPos", error="connection refused")
        )
        assert got and got[0] == "ACTIVITY"
        assert "CopyPos" in got[1] and "connection refused" in got[1]

    def test_a_failed_status_counts_even_with_no_error_text(self):
        got = classify(ev(kind="activity", activityName="CopyPos", status="Failed"))
        assert got and got[0] == "ACTIVITY"

    def test_a_retry_is_reported_even_though_it_succeeded(self):
        # "It worked eventually" is the finding: a green run that retried is
        # not the same as a green run that did not, and only this says so.
        got = classify(ev(kind="activity", activityName="CopyErp", retryAttempt=2))
        assert got and got[0] == "RETRY"
        assert "attempt 2" in got[1]

    def test_a_clean_activity_is_silent(self):
        assert (
            classify(
                ev(
                    kind="activity",
                    activityName="CopyPos",
                    status="Succeeded",
                    durationInSeconds=1.2,
                )
            )
            is None
        )

    def test_an_error_outranks_a_retry(self):
        # Both apply; the failure is the one a human needs first.
        got = classify(
            ev(kind="activity", activityName="X", error="boom", retryAttempt=3)
        )
        assert got and got[0] == "ACTIVITY"


class TestLineage:
    @pytest.mark.parametrize("producer", sorted(PRODUCERS))
    def test_every_known_producer_is_silent(self, producer):
        assert classify(ev(kind="lineage", producer=producer)) is None

    def test_an_unknown_producer_is_reported(self):
        # The replacement for the rule that could never fire. If the emulator
        # grows a producer this has not been taught, saying so beats silently
        # classifying it as neither evidence nor claim.
        got = classify(ev(kind="lineage", producer="Telepathy"))
        assert got and got[0] == "LINEAGE"
        assert "Telepathy" in got[1]

    def test_an_absent_producer_is_reported_rather_than_ignored(self):
        # It cannot occur today. If a future emulator publishes one, silence
        # would be the wrong answer.
        got = classify(ev(kind="lineage"))
        assert got and got[0] == "LINEAGE"


class TestSummary:
    def test_evidence_and_claims_are_counted_apart(self):
        s = summarise(
            [
                ev(kind="lineage", producer="Warehouse"),
                ev(kind="lineage", producer="Reported"),
                ev(kind="lineage", producer="Reported"),
                ev(kind="table"),
            ]
        )
        assert s["observed"] == 1
        assert s["claimed"] == 2
        assert s["kinds"]["lineage"] == 3
        assert not s["all_claimed"]

    def test_a_wholly_self_reported_graph_is_flagged(self):
        # No single event is wrong; the RUN is. A per-event rule cannot see
        # this, which is why the summary exists at all.
        s = summarise([ev(kind="lineage", producer="Reported")] * 5)
        assert s["all_claimed"]

    def test_a_run_with_no_lineage_is_not_called_self_reported(self):
        # "No edges" and "all edges are claims" are different problems, and
        # labelling the first as the second would send someone the wrong way.
        assert not summarise([ev(kind="table")])["all_claimed"]


class TestVocabularyMatchesTheEmulator:
    """The rules are only as good as the constants they are written against.

    Both original defects came from guessing the emulator's vocabulary. This
    reads it back out of the source, so a rename upstream fails here rather
    than turning a rule into a no-op nobody notices.
    """

    # RELATIVE TO THIS REPO, not a machine path. This was hardcoded to one
    # absolute location, so the day the family moved to ~/calvinchengx/emulators
    # every check below turned into a skip — and a skip is the one outcome that
    # looks like success. The rule these tests enforce (the emulator's own
    # vocabulary, read from its source) went unenforced and nothing said so.
    #
    # A sibling checkout is the assumption, which survives moving the pair
    # anywhere as long as they move together. FABRIC_EMULATOR_REPO overrides it
    # for a layout that separates them.
    EMULATOR = pathlib.Path(
        os.environ.get(
            "FABRIC_EMULATOR_REPO",
            pathlib.Path(__file__).resolve().parents[1].parent / "fabric-emulator",
        )
    )

    def _emulator_source(self, rel: str) -> str:
        p = self.EMULATOR / rel
        if not p.exists():
            pytest.skip(f"fabric-emulator not checked out beside this repo ({rel})")
        return p.read_text(encoding="utf-8")

    @staticmethod
    def _defines(src: str, name: str, value: str) -> bool:
        # gofmt ALIGNS const blocks, so `JobCompleted  = "Completed"` carries
        # two spaces. An exact-string match fails on formatting rather than on
        # the drift it is meant to catch.
        import re

        return re.search(rf'\b{name}\s+=\s+"{value}"', src) is not None

    def test_job_started_is_the_value_the_bus_publishes(self):
        src = self._emulator_source("internal/store/bus.go")
        assert self._defines(src, "JobStarted", "Started"), (
            "the bus's start value moved; JOB_FINE must move with it or every "
            "job start is reported as suspicious"
        )

    def test_the_job_status_enum_is_what_we_allow(self):
        src = self._emulator_source("internal/store/jobs.go")
        for name, value in (
            ("JobNotStarted", "NotStarted"),
            ("JobInProgress", "InProgress"),
            ("JobCompleted", "Completed"),
            ("JobFailed", "Failed"),
            ("JobCancelled", "Cancelled"),
        ):
            assert self._defines(src, name, value), f"{name} moved"
        assert "Failed" not in JOB_FINE and "Cancelled" not in JOB_FINE

    def test_every_producer_the_emulator_defines_is_classified(self):
        src = self._emulator_source("internal/store/lineage.go")
        import re

        defined = set(re.findall(r'Producer\w* += +"(\w+)"', src))
        assert defined, "no producer constants parsed — the source shape moved"
        missing = defined - PRODUCERS
        assert not missing, (
            f"the emulator defines producer(s) this observer cannot classify: "
            f"{sorted(missing)} — an unknown producer is reported, but silence "
            f"about a known one would be a false all-clear"
        )
        assert set() == PRODUCERS_OBSERVED & PRODUCERS_CLAIMED, (
            "a producer cannot be both evidence and a claim"
        )

    def test_an_empty_producer_cannot_reach_the_stream(self):
        # The rule that could never fire. Pinned so that if the default is
        # removed upstream, this says so.
        src = self._emulator_source("internal/store/lineage.go")
        assert "e.Producer = ProducerCopy" in src, (
            "CreateLineageEdge no longer defaults an empty producer — an edge "
            "with none can now reach the stream, and the observer's handling "
            "of that case stops being theoretical"
        )


def test_the_observer_runs_as_a_script():
    """It is invoked from the demo, so it must at least import and start."""
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "observe.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # No --help is defined; what matters is that it does not fail to import.
    assert "Traceback" not in r.stderr or "urlopen" in r.stderr, r.stderr[:400]


def test_findings_survive_a_round_trip_through_jsonl():
    """The stream is written to disk so a finding can be examined, not just
    described. A file that cannot be read back makes that claim false."""
    events = [ev(kind="lineage", producer="Reported"), ev(kind="dropped", dropped=3)]
    text = "\n".join(json.dumps(e) for e in events)
    back = [json.loads(ln) for ln in text.splitlines()]
    assert [classify(e) for e in back][1][0] == "DROPPED"


class TestTheDemoHarness:
    """`make demo` had no test, and it is the piece most able to fail silently.

    Its failure mode is a video: a green exit beside a recording of a dead
    terminal, or of a graph that never drew. Two such defects shipped in the
    first draft and were caught by opening the frame, not by the counters —
    which is why the recorder asserts both panes SEPARATELY and this pins that
    it still does.
    """

    RECORDER = ROOT / "capture" / "sidebyside.js"
    DRIVER = ROOT / "scripts" / "demo.py"

    def test_both_panes_are_asserted_and_neither_stands_in_for_the_other(self):
        src = self.RECORDER.read_text(encoding="utf-8")
        assert "RENDERED" in src and "TERMINAL" in src, (
            "the recorder must report each pane; one pass/fail hides which "
            "half of the video is missing"
        )
        # The exit code requires BOTH. A recorder that exits 0 with a dead
        # terminal produces a green run and an unusable artifact.
        assert "rendered && attached && saved" in src, (
            "the recorder's exit code must require both panes and the video"
        )

    def test_the_terminal_is_read_from_the_xterm_buffer(self):
        # xterm.js renders to <canvas>: `.xterm-rows > div` matches nothing and
        # innerText is empty, so an assertion written against the DOM CANNOT
        # pass. The first version reported TERMINAL false on a working pane.
        src = self.RECORDER.read_text(encoding="utf-8")
        assert "window.term?.buffer?.active" in src, (
            "the terminal check must read xterm's buffer — the DOM and the "
            "canvas both report nothing for a working terminal"
        )
        # In CODE, not in the comment that explains why it is wrong — the
        # first version of this test flagged its own documentation.
        code = "\n".join(
            ln for ln in src.splitlines() if not ln.strip().startswith("//")
        )
        for call in ("locator(", "querySelector"):
            assert f"{call}'.xterm-rows" not in code and (
                f'{call}".xterm-rows' not in code
            ), (
                "a DOM row selector is back; it cannot match, so the check "
                "would silently report an empty terminal"
            )

    def test_ttyd_is_not_started_with_once(self):
        # `-o` disconnects the client when the command exits, painting
        # "Press ⏎ to Reconnect" over the pane for the tail of every video —
        # and it deadlocks the driver, which waits for the stop file that is
        # only written after ttyd exits.
        src = self.DRIVER.read_text(encoding="utf-8")
        assert '"-o"' not in src, (
            "ttyd -o is back: it overlays a reconnect prompt on the recording "
            "and deadlocks the wait"
        )

    def test_completion_is_a_marker_file_not_the_ttyd_lifetime(self):
        src = self.DRIVER.read_text(encoding="utf-8")
        assert ".demo-exit" in src and "marker.exists()" in src, (
            "the driver must wait on the command's own exit marker; waiting on "
            "ttyd deadlocks"
        )

    def test_the_inner_recorder_is_disabled(self):
        # Two Playwright contexts filming the same portal contend, and the
        # second video is the one nobody wanted.
        assert '"CAPTURE": "0"' in self.DRIVER.read_text(encoding="utf-8")

    def test_the_driver_refuses_rather_than_recording_half_a_frame(self):
        src = self.DRIVER.read_text(encoding="utf-8")
        assert "ttyd is not installed" in src, (
            "without ttyd this would silently record the graph alone"
        )
