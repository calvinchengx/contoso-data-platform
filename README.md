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
make fixtures    # install the generators published by the pinned release
make sources     # materialise the bytes the vendor APIs serve
make up          # start the stack
make verify      # run the platform end to end
```

`fixtures` and `sources` are not optional and not one-time. The vendors serve
files from `sources/_data/`, which is gitignored — ~194 MB of generated data
does not belong in git — so a fresh clone has nothing to serve until these two
run. Skipping them was how CI first went red: mokapi could not read its own
fixture, fell back to generating bodies from the OpenAPI schema, and answered a
deliberately wrong API key with `200`.

## The source systems

**Four vendors, four different problems.** A platform that ingests one source
proves it can ingest one source. These are deliberately not variations on a
theme — each arrives by a different transport, in a different format, with its
own credential, and each brings a failure mode the others cannot.

| vendor | transport | format | the problem it creates |
|---|---|---|---|
| **Contoso POS** | REST, paged | CSV + JSON Lines | redeliveries, malformed rows, country spelt five ways, 3% with no email |
| **Contoso Web** | REST, paged | JSON arrays, **nested** | no customer id — accounts are keyed on email |
| **Contoso ERP** | Debezium → Redpanda | CDC change stream | a database that *changes*, not a file drop |
| **Contoso Reference** | REST, **not** paged | Parquet (binary) | master data everything else is reported against |

Each gets **its own credential**, kept in Key Vault and never in the tree, and
appears as its own connection in lineage. The three REST vendors get **an
instance of their own** rather than sharing one — that is not ceremony: two
companies do not share a process, one instance puts every vendor under a single
memory ceiling, and a shared key would prove nothing about either door. ERP
authenticates to Postgres instead, with its password from the same vault.

**Contoso POS** — 102,000 customer rows across 101 columns and 255,000 order
events, ~162 MB. The vendor redelivers a share of its rows, so bronze holds more
rows than there are customers; that surplus is what silver's dedupe exists to
remove, and a bronze that arrived already clean would let silver pass its own
assertions while testing nothing.

**Contoso Web** — 40,000 accounts and 90,000 orders that flatten to 226,544
lines, ~32 MB. The storefront thinks in baskets, so orders arrive **nested** and
stay that way in bronze. It has no customer id at all: **22,000 of its shoppers
are also POS customers** and neither system knows it. Resolving them is the
problem this vendor exists to create. It also ships 5% cancelled orders, 15% of
timestamps with a real UTC offset, and 2% of lines pointing at SKUs no catalogue
publishes — all of which survive into the reporting pack rather than being
filtered on the way.

**Contoso ERP** — 93,571 change events. A Parquet file *describing* a change log
is a simulation of CDC; a Postgres table with Debezium on it is the thing
itself, which is why the connector is registered **before** the history replays.

**Contoso Reference** — the group data office, ~20 KB and the most fragile of
the four. It publishes the product rollup (8 SKUs → 2 departments → 2 reporting
segments) and daily FX (132 rows, 4 currencies). Two details matter: it serves
**Parquet**, which mokapi's ordinary response path silently corrupts, so the
vendor publishes an `X-Content-SHA256` the ingest step verifies; and **FX exists
for trading days only** — 33 of 45 calendar days — so weekend orders have no
published rate and silver carries the last one forward, recording per row
whether a rate was quoted or assumed.

## Rules

[RULES.md](RULES.md) holds the rules this codebase is built on — Fabric-first,
engine-side transforms, secrets in Key Vault, one file for the emulator/real
difference. Each rule names the test that enforces it, and says `judgement`
where nothing does.

## What it needs to run

The full stack is **17 services** — the emulator family, Sail, a Spark agent, a
SQL Server sidecar, the source systems (three mokapi instances, Postgres,
Redpanda, Debezium) and OpenMetadata with its own Postgres and OpenSearch.
Budget **~8 GB** to Docker; OpenSearch alone asks for a 1 GB heap.

Two limits worth knowing before you start:

- **SQL Server is amd64-only.** On Apple silicon it runs translated; on an
  arm64 Linux host gold cannot run at all. That is SQL Server's distribution,
  not this platform.
- **The catalog is part of `make verify`**, not a separate command — a catalog
  exercised only on request is one nobody hears about when it breaks.

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

`versions.env` pins every image, and the file itself is the list — this section
used to restate four of them and had drifted seven minor versions behind before
anyone noticed. A README that copies a pinned version is a second source of
truth with no check keeping it honest, so it names none.

Read [`versions.env`](versions.env) for the current pins. The family's own
defaults live in [**azure-emulators**](https://github.com/calvinchengx/azure-emulators), which is the bill of materials these
are expected to agree with.

`versions.env` pins more than these four — Sail and the Spark agent move in
lockstep with the emulator, the ERP stack (Postgres, Redpanda, Debezium) is
pinned independently, and SQL Server is pinned **by digest** because that
sidecar *is* the Warehouse engine: an unannounced image change would move T-SQL
behaviour underneath the assertions.

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

Scaffold. It runs end to end today and still grows in phases — what it covers:

| | |
|---|---|
| sources | four vendors — three mokapi (REST) · Postgres + Debezium + Redpanda (CDC) |
| lakehouse | landing → bronze → silver → gold |
| identity | web accounts resolved against POS customers on email |
| serving | semantic model → Power BI, queried with DAX over `executeQueries` |
| governance | OpenMetadata: API, database and messaging services, lineage from source |
| capture | the Data flow graph recorded *while* it runs; the catalog after |

Working today, against the release pinned in [`versions.env`](versions.env):
`make verify` runs the platform **end to end from a cold `make down`** — 16
steps, no manual intervention.

> **Currently red on `main` (2026-08-11).** `platform/bronze.py` imports `T` from
> a module that never exported it, so it raises at load and `make verify` stops
> at step 9. It reached main because `acceptance.yml` runs on a schedule and
> `workflow_dispatch`, never on `pull_request` — so no PR check executes a
> platform step. The import is fixed in the 0.22.0 bump; the step then fails on
> a column count (the notebook produces 102, the vendor's fixture declares 101),
> which is still open. Stated here rather than left for a reader to discover,
> because "16 of 16" is the claim this README exists to make. The four vendors serve ~194 MB of seeded export,
it lands in OneLake byte-identical, bronze and silver are computed by a **Fabric
notebook** on Spark, gold builds in the Warehouse via **dbt-fabric** with 60
data tests, and the semantic model answers DAX over the Power BI
`executeQueries` wire.

The reporting pack is the point of the whole thing: revenue in USD by
**financial year** (Contoso's starts 1 April), by **product segment** and by
**customer segment**, across both selling systems — 22,000 people resolved
between them.

**XMLA is the one named gap.** `make verify` runs a real ADOMD.NET client
against the model and reports `no endpoint` — deferred in `docs/24`. It is
reported rather than skipped, because a surface nobody asks about is a surface
nobody hears about when it breaks.
