"""Report what is ready and what is not — on any of the three platforms.

A doctor that only prints when something is wrong teaches you nothing about
what it checked. Every line below says what was examined and what was found,
so "green" is a statement rather than the absence of a complaint.
"""

import platform
import shutil
import subprocess
import pathlib
import sys

import release_info as rel

OK, BAD, PEND = "  ok  ", " MISS ", "PENDING"


def tool(name, args, hint):
    exe = shutil.which(name)
    if not exe:
        return BAD, f"not on PATH — {hint}"
    try:
        out = subprocess.run([name, *args], capture_output=True, text=True, timeout=60)
        first = (out.stdout or out.stderr).strip().splitlines()
        return OK, first[0] if first else exe
    except Exception as exc:
        return BAD, f"found at {exe} but would not run: {exc}"


def main():
    rows = []
    rows.append(("platform", OK, f"{platform.system()} {platform.machine()}"))
    rows.append(("python", OK, sys.version.split()[0]))
    rows.append(
        (
            "docker",
            *tool(
                "docker", ["--version"], "install Docker Desktop, or docker-ce on Linux"
            ),
        )
    )
    rows.append(
        ("uv", *tool("uv", ["--version"], "https://docs.astral.sh/uv/getting-started/"))
    )
    rows.append(
        (
            "make",
            *tool("make", ["--version"], "Windows: winget install ezwinports.make"),
        )
    )

    v = rel.version()
    rows.append(("pinned release", OK, f"fabric-emulator {v}"))

    # The wheels are published by the emulator's release workflow. Until a
    # release carrying them ships, this is PENDING — a real, named, temporary
    # state. Not an error to be silenced, and not something to skip past.
    all_there, per = rel.wheels_published(v)
    if all_there is None:
        rows.append(("fixture wheels", PEND, "could not reach github.com"))
    elif all_there:
        rows.append(("fixture wheels", OK, f"published for {v}"))
    else:
        missing = [u.rsplit("/", 1)[1] for u, s in per.items() if not s]
        rows.append(
            ("fixture wheels", PEND, f"not published for {v} — {', '.join(missing)}")
        )

    # THE VENDORS ARE NOT OPTIONAL, and their absence does not announce itself.
    # Without contoso-sources materialised, mokapi still starts and still
    # answers 200 -- it generates bodies from the OpenAPI schema instead of
    # serving the fixture. The pipeline would then land invented data, build a
    # green medallion on it, and publish numbers that agree with nothing.
    import os

    root = pathlib.Path(__file__).resolve().parent.parent
    src = pathlib.Path(os.environ.get("SOURCES", root.parent / "contoso-sources"))
    if not (src / "sources.yaml").exists():
        rows.append(("vendors", BAD,
                     f"no declaration at {src / 'sources.yaml'} — clone "
                     f"calvinchengx/contoso-sources beside this repo, or set SOURCES="))
    elif not (src / "_data").is_dir() or not any((src / "_data").iterdir()):
        rows.append(("vendors", BAD,
                     f"{src / '_data'} is empty — run `make sources` "
                     f"(it delegates to that repo)"))
    else:
        rows.append(("vendors", OK, f"declared and materialised at {src}"))

    width = max(len(r[0]) for r in rows)
    for name, status, detail in rows:
        print(f"[{status}] {name.ljust(width)}  {detail}")

    missing_tools = [n for n, s, _ in rows if s == BAD]
    if missing_tools:
        print(f"\nmissing: {', '.join(missing_tools)} — see the lines above")
        return 1
    pending = [n for n, s, _ in rows if s == PEND]
    if pending:
        print(f"\npending (not a failure): {', '.join(pending)}")
        print(
            "`make fixtures` and `make verify` need the wheels; `make test` does not."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
