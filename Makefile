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

.DEFAULT_GOAL := help
.PHONY: help doctor fixtures sources up down config verify test clean

help:  ## Show the targets
	@uv run --no-project python scripts/help.py

doctor:  ## Check prerequisites and report what is and is not ready
	@uv run --no-project python scripts/doctor.py

fixtures:  ## Install the seeded generators published by the pinned release
	@uv run --no-project python scripts/fixtures.py

sources:  ## Materialise the vendor exports the source APIs serve
	@uv run python scripts/materialise_sources.py

up:  ## Start the emulator family and the source systems
	@uv run --no-project python scripts/compose.py up -d

down:  ## Stop everything and remove volumes
	@uv run --no-project python scripts/compose.py down -v

config:  ## Show the resolved compose config (proves the pin)
	@uv run --no-project python scripts/compose.py config

verify:  ## Run the platform end to end against the pinned release
	@uv run python platform/pipeline.py

test:  ## The repo's own tests — version lockstep, boundaries, config
	@uv run --with pytest pytest -q tests

clean:  ## Remove build and run artifacts
	@uv run --no-project python scripts/clean.py
