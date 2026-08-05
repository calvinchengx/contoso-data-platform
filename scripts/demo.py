#!/usr/bin/env python3
"""Record the platform running: the terminal and the flow graph, side by side.

WHY THIS IS NOT `make verify`. The pipeline records the flow view itself, from
inside, because only it knows when the run begins. That produces a graph filling
in with nothing to explain it. This drives the run from OUTSIDE instead — a
terminal in ttyd, filmed beside the portal — so the line of output and the node
that lights up are the same event seen twice.

The inner recorder is therefore switched OFF (CAPTURE=0). Two Playwright
contexts filming the same portal would contend for it, and the second video
would be the one nobody wanted.

ORDER MATTERS, and ttyd gives it to us for free: it spawns the command when a
client CONNECTS, so the run begins the moment the recorder attaches. Nothing
happens off camera — provisioning and the vendor pull are the steps that explain
what the rest is doing, and they are the ones a late-starting recorder misses.

COMPLETION IS A MARKER FILE, not `ttyd.wait()`. `ttyd -o` exits when the client
disconnects, and the client disconnects only after this script writes the stop
file — which it would be waiting to do. That is a deadlock, and it would have
been discovered twenty minutes into a recording.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHOTS = ROOT / "capture" / "shots"
STOP = SHOTS / ".stop"
IMAGE = "contoso-capture"
TTYD_PORT = int(os.environ.get("TTYD_PORT", "7681"))


def die(msg: str) -> None:
    raise SystemExit(f"demo: {msg}")


def wait_for(url: str, what: str, timeout: int = 60) -> None:
    """Gate on the thing being reachable, never on a sleep.

    A fixed wait passes on this machine and fails on a slower one, and — worse
    — passes with the service half-up, which films a blank pane.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except urllib.error.HTTPError:
            return  # answering at all is enough; 4xx is still a listener
        except Exception:
            time.sleep(0.5)
    die(f"{what} never came up at {url}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--command", default="make verify", help="what the filmed terminal runs"
    )
    # ADDITIVE, not a replacement. The composited recorder still works and is
    # what runs against an emulator without the terminal routes; --in-pane needs
    # one that has them (compose/terminal.yml) and films the portal alone.
    ap.add_argument(
        "--in-pane",
        action="store_true",
        help="film the portal's own terminal pane instead of compositing two panes",
    )
    args = ap.parse_args()

    if not shutil.which("ttyd"):
        die(
            "ttyd is not installed — `brew install ttyd`. It is the terminal "
            "pane; without it this would silently record the graph alone."
        )
    if not shutil.which("docker"):
        die("docker is not installed")

    SHOTS.mkdir(parents=True, exist_ok=True)
    STOP.unlink(missing_ok=True)

    subprocess.run(
        ["docker", "build", "-q", "-f", "docker/capture/Dockerfile", "-t", IMAGE, "."],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )

    # `-W` so the pane is not read-only — a viewer watching a demo should be
    # able to believe it is a terminal.
    #
    # NO `-o`. It disconnects the client the instant the command exits, and
    # ttyd then paints "Press ⏎ to Reconnect" across the pane — so the tail of
    # every recording is a dead terminal beside a finished graph. Caught by
    # looking at the frame; the pane still asserted TRUE, because the buffer it
    # reads is the one behind that overlay.
    #
    # The command writes its exit code to a marker file. That, not ttyd's own
    # lifetime, is how this script knows the run ended: see the module docstring
    # for why waiting on ttyd deadlocks.
    # Matches compose/terminal.yml's pinned token; the pane needs it to connect.
    token = os.environ.get("TERMINAL_TOKEN", "contoso-demo-token")
    marker = SHOTS / ".demo-exit"
    marker.unlink(missing_ok=True)
    # Hold the shell open after the marker so the final output stays on screen
    # while the recorder flushes. Without it the pane goes blank at exactly the
    # moment a viewer wants to read the summary.
    shell = f"cd {ROOT} && {args.command}; echo $? > {marker}; sleep 600"
    # TERMINAL=1 travels INTO the filmed shell: the pipeline runs its own
    # `docker compose`, and without the same overlay it recreates the
    # emulator out from under the pane it is being filmed in.
    env = {**os.environ, "CAPTURE": "0"}
    if args.in_pane:
        env["TERMINAL"] = "1"
    ttyd = subprocess.Popen(
        [
            "ttyd",
            "-p",
            str(TTYD_PORT),
            "-W",
            "-t",
            "fontSize=15",
            "-t",
            "theme={'background':'#0b0e14'}",
            "bash",
            "-lc",
            shell,
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for(f"http://localhost:{TTYD_PORT}", "ttyd")
        print(f"==> ttyd on :{TTYD_PORT} running `{args.command}`", flush=True)

        rec = subprocess.Popen(
            [
                "docker",
                "run",
                "--network",
                "host",
                "-v",
                f"{ROOT / 'capture'}:/capture",
                "-e",
                f"TTYD_URL=http://localhost:{TTYD_PORT}",
                "-e",
                f"PORTAL_URL={os.environ.get('PORTAL_URL', 'https://localhost:9443')}",
                "-e",
                f"TERMINAL_TOKEN={token}",
                IMAGE,
                "/capture/inpane.js" if args.in_pane else "/capture/sidebyside.js",
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        print("==> recording", flush=True)

        # Poll for the marker. No timeout of our own: `make verify` takes as
        # long as the platform takes, and a cap here would truncate a slow run
        # into a video of a partial pipeline. The recorder has its own ceiling.
        while not marker.exists():
            if rec.poll() is not None:
                die(
                    f"the recorder exited early ({rec.returncode}) — nothing is "
                    f"filming, so stopping rather than running on blind"
                )
            time.sleep(2)
        rc = int(marker.read_text(encoding="utf-8").strip() or "1")
        print(f"==> `{args.command}` finished (exit {rc})", flush=True)

        STOP.write_text("stop", encoding="utf-8")
        out, _ = rec.communicate(timeout=300)
        for line in (out or "").splitlines():
            # NON2XX included: the recorder logs every failing response its
            # own page sees, and a filter that dropped those lines is how a
            # 404 got hunted blind for five runs.
            marks = (
                "RENDERED",
                "TERMINAL",
                "VIDEO",
                "WATCHED",
                "WATCHING",
                "TOURED",
                "NON2XX",
            )
            if line.startswith(marks):
                print(f"    {line}", flush=True)
        if rec.returncode != 0:
            die(
                f"the recorder failed (exit {rec.returncode}) — a green run with "
                f"no usable video is not a recording"
            )
        if rc != 0:
            die(f"`{args.command}` failed (exit {rc}); the video shows the failure")
    finally:
        if ttyd.poll() is None:
            ttyd.terminate()

    name = "99-in-pane.webm" if args.in_pane else "99-side-by-side.webm"
    video = SHOTS / name
    print(f"==> {video.relative_to(ROOT)} ({video.stat().st_size:,} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
