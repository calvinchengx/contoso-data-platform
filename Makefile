# The only interface. Windows, macOS and Linux run the SAME targets.
#
# Windows users:  winget install ezwinports.make
# Everyone needs: Docker, and uv (https://docs.astral.sh/uv/)
#
# EVERY RECIPE IS A ONE-LINER over `docker`, `uv` or `python`. Nothing here may
# use a shell builtin, a pipe, `rm -rf`, backticks or an `if`: GNU Make on
# Windows runs recipes through cmd.exe, where none of that means what it means
# on a POSIX shell. Logic belongs in scripts/, which is Python, which is the
# only thing all three platforms genuinely agree on.
#
# The test for whether a recipe belongs here: could it run unchanged in cmd.exe?
# If not, it goes in a script.
#
# UV, STRICTLY. pyproject.toml is the manifest and uv.lock is committed, so a
# clone resolves to the same versions on all three platforms. No bare python,
# no pip, no `--with` (which resolves fresh every run and would silently change
# the test suite between two runs of the same commit).
#
#   --frozen           use the committed lock; never resolve or update it
#   --no-sync          do not touch the environment
#   --no-project       stdlib-only diagnostics, so `make doctor` still works
#                      when the environment is broken or absent
#
# `make fixtures` installs the generator wheels with `uv pip install`, outside
# the lock — that is deliberate, because WHICH release they came from is the
# thing under test and pinning them in the lock would defeat it.
#
# MEASURED, after getting this wrong twice:
#
#   uv run --frozen   (lock unchanged)  -> fixtures SURVIVE
#   uv sync           (explicit)        -> fixtures EVICTED
#
# So `uv sync` prunes anything not in the lock, and re-running `make fixtures`
# after one is required rather than optional. `--no-sync` on the run targets
# keeps a step from reconciling the environment out from under them.

.DEFAULT_GOAL := help
.PHONY: help doctor fixtures sources up down config lint fmt govern verify test clean

help:  ## Show the targets
	@uv run --no-project python scripts/help.py

doctor:  ## Check prerequisites and report what is and is not ready
	@uv run --no-project python scripts/doctor.py

fixtures:  ## Install the seeded generators published by the pinned release
	@uv run --no-project python scripts/fixtures.py

sources:  ## Materialise the vendor exports the source APIs serve
	@uv run --frozen --no-sync python scripts/materialise_sources.py

up:  ## Start the emulator family and the source systems
	@uv run --no-project python scripts/compose.py up -d

down:  ## Stop everything and remove volumes
	@uv run --no-project python scripts/compose.py down -v

config:  ## Show the resolved compose config (proves the pin)
	@uv run --no-project python scripts/compose.py config

govern:  ## Catalog the platform in OpenMetadata (also runs inside `make verify`)
	@uv run --frozen --no-sync python platform/govern.py

verify:  ## Run the platform end to end against the pinned release
	@uv run --frozen --no-sync python platform/pipeline.py

lint:  ## ruff (lint + format check) and ty (types)
	@uv run --frozen ruff check .
	@uv run --frozen ruff format --check .
	@uv run --frozen ty check .

fmt:  ## Apply ruff's formatting and safe fixes
	@uv run --frozen ruff check --fix .
	@uv run --frozen ruff format .

test:  ## The repo's own tests — version lockstep, boundaries, config
	@uv run --frozen pytest -q tests

clean:  ## Remove build and run artifacts
	@uv run --no-project python scripts/clean.py
