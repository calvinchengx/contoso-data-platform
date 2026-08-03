# Contoso Data Platform

A complete analytics platform built on [`fabric-emulator`](https://github.com/calvinchengx/fabric-emulator) —
from **real source systems** through a medallion lakehouse to a **semantic model
and Power BI**, with everything catalogued in **OpenMetadata**.

It runs against a **published release**, never a checkout. That is the point:
this repository is a *consumer*. It has no access to the emulator's source, so
anything that works here works for anyone.

```sh
git clone https://github.com/calvinchengx/contoso-data-platform
cd contoso-data-platform
make doctor      # what is ready, and what is not
make up          # start the stack
make verify      # run the platform end to end
```

## Rules

[RULES.md](RULES.md) holds the rules this codebase is built on — Fabric-first,
engine-side transforms, secrets in Key Vault, one file for the emulator/real
difference. Each rule names the test that enforces it, and says `judgement`
where nothing does.

## Requirements

Three tools, on any of the three platforms:

| | |
|---|---|
| **Docker** | Docker Desktop on macOS/Windows, `docker-ce` on Linux |
| **uv** | https://docs.astral.sh/uv/getting-started/ |
| **make** | preinstalled on macOS/Linux · Windows: `winget install ezwinports.make` |

**Windows, macOS and Linux run the identical `make` targets.** That is asserted,
not assumed: CI runs `make help`, `make test`, `make doctor` and `make config` on
all three on every push, and a test rejects any Makefile recipe that would not
survive `cmd.exe` — GNU Make on Windows runs recipes through it, so a pipe or an
`rm` in a recipe is a bug that only ever bites the Windows user.

Run `make` for the target list.

## How it is pinned

`versions.env` pins every image — and there are **four**, not one:

```
FABRIC_EMULATOR_VERSION=0.13.0
ENTRA_EMULATOR_VERSION=0.3.0
KEYVAULT_EMULATOR_VERSION=0.3.0
MOKAPI_VERSION=0.50.0
```

The emulator family ships on independent cadences, so a single pin cannot
describe the stack — assuming otherwise is how this repo first failed to start.
`docker compose --env-file` reads the file directly, so the pins are stated once
and nothing translates them.

The generators are the boundary between the two repositories:

```
contoso-fixtures            published by fabric-emulator's release workflow,
contoso-fixtures-advanced   installed here at the pinned tag
```

Those are the same seeded generators the emulator's own medallion examples use.
One generator means a number asserted there and a number asserted here cannot
quietly describe different datasets. `make fixtures` installs them and refuses
to continue if the installed version and the pin disagree.

This repository deliberately does **not** use `common.py`, the client plumbing
that ships inside those packages. It writes its own calls against the published
API, because a consumer would have to — and a test enforces it.

## Status

Scaffold. The trigger, the schedule guard, the failure attribution and the
cross-platform gate are in place and provable; the platform itself lands in
phases:

| | |
|---|---|
| sources | mokapi (REST) · Postgres + Debezium + Redpanda (CDC) |
| lakehouse | landing → bronze → silver → gold |
| serving | semantic model → Power BI |
| governance | OpenMetadata: API, database and messaging services, lineage from source |
| capture | the Data flow graph recorded *while* it runs; the catalog after |

Working today, against **fabric-emulator 0.13.0**: the vendor's REST API serves
169.8 MB of seeded export over HTTP, a workspace and lakehouse are provisioned,
and the export lands in OneLake byte-identical — verified by reading it back.

`make doctor` reports **PENDING** for the fixture wheels: they are published
from the first `fabric-emulator` release after the packaging landed, and
`0.13.0` predates it. That is a real, named, temporary state — not a
failure, and not something to skip past.
