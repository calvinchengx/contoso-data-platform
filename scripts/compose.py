"""Run docker compose with the pinned release in the environment.

The pin lives in .emulator-version. Reading it from the Makefile would need
$(shell cat ...) — which is not a thing on cmd.exe, where GNU Make on Windows
runs its recipes. So the Makefile stays a one-liner and the logic lives here,
where `pathlib.read_text(encoding="utf-8")` means the same on all three platforms.
"""

import os
import pathlib
import shutil
import subprocess
import sys
import urllib.request

import release_info as rel

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = [
    "compose/docker-compose.yml",
    "compose/sources.yml",
    "compose/governance.yml",
]

# TERMINAL=1 films the run inside the portal's own terminal pane rather than
# beside a separately launched ttyd. Opt-in because it points the emulator at a
# shell: the overlay sets FABRIC_TERMINAL_URL, and without it the emulator does
# not mount the terminal routes at all.
if os.environ.get("TERMINAL") == "1":
    FILES.append("compose/terminal.yml")


WHEELS = ROOT / ".wheels"


def stage_product_wheel() -> None:
    """Put the data product where the Spark agent will install it.

    bronze and silver import `contoso_product`, and they run on the agent, not
    in this process. The notebooks declare a Fabric Environment, which is what
    real Fabric acts on; the emulator resolves that binding for a notebook run
    but does not apply it, so the agent needs the wheel by its documented
    `/opt/wheels` fallback instead.

    THE VERSION IS THE ONE THIS PLATFORM INSTALLED. Read from the environment
    rather than pinned here, so the engine cannot end up on a different release
    of the product than the client, which would be a difference no test would
    catch and every number would hide.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        v = version("contoso-data-product")
    except PackageNotFoundError:
        # `make up` before `uv sync`. The agent starts without the product and
        # the bronze step fails naming it, which is better than a silent skip.
        print("contoso-data-product is not installed; skipping the wheel stage")
        return

    name = f"contoso_data_product-{v}-py3-none-any.whl"
    url = (
        "https://github.com/calvinchengx/contoso-data-product/releases/download/"
        f"v{v}/{name}"
    )
    WHEELS.mkdir(exist_ok=True)
    # Anything else is a wheel for a version we are no longer on. Left behind,
    # the agent would install both and the newer one would not reliably win.
    for stale in WHEELS.glob("contoso_data_product-*.whl"):
        if stale.name != name:
            stale.unlink()
    dest = WHEELS / name
    if dest.is_file():
        return
    print(f"staging {name} for the Spark agent")
    with urllib.request.urlopen(url) as r, dest.open("wb") as f:
        shutil.copyfileobj(r, f)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: compose.py <up|down|config|ps> [args...]")
    args = sys.argv[1:]
    if args and args[0] == "up":
        stage_product_wheel()
    env = dict(os.environ)
    # The governance profile is on by default. It is the heaviest part of the
    # stack — OpenSearch alone wants a 1 GB heap — but a catalog that only a
    # separate command exercises is one nobody hears about when it breaks.
    cmd = [
        "docker",
        "compose",
        "--env-file",
        rel.VERSIONS.name,
        "--profile",
        "governance",
    ]
    for f in FILES:
        cmd += ["-f", f]
    cmd += args
    print("$", " ".join(cmd), f"   (fabric-emulator {rel.version()})")
    return subprocess.run(cmd, cwd=ROOT, env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
