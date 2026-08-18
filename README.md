# Contoso Fabric Platform

[![CI](https://github.com/calvinchengx/fabric-platform-notebook-pipelines/actions/workflows/ci.yml/badge.svg)](https://github.com/calvinchengx/fabric-platform-notebook-pipelines/actions/workflows/ci.yml)
[![Acceptance](https://github.com/calvinchengx/fabric-platform-notebook-pipelines/actions/workflows/acceptance.yml/badge.svg)](https://github.com/calvinchengx/fabric-platform-notebook-pipelines/actions/workflows/acceptance.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

A complete analytics platform built on [`fabric-emulator`](https://github.com/calvinchengx/fabric-emulator) —
from **real source systems** through a medallion lakehouse to a **semantic model
and Power BI**, with everything catalogued in **OpenMetadata**.

This is the proof that an AI coding agent can deliver a real data product end
to end: give it a source system's metadata, the business goal a stakeholder
needs answered, and sample or synthetic data, and it builds the whole path,
source to landing to medallion to a served analytics layer, against the
emulator first and real Fabric second, with no code changes between them. What
this repository took to build, months of tenant-bound trial and error compressed
into days by iterating offline first, is the point of the whole family.

The trust chain is the same one production uses:
[entra-emulator](https://github.com/calvinchengx/entra-emulator) issues every
token, and [azure-keyvault-emulator](https://github.com/calvinchengx/azure-keyvault-emulator)
holds every credential. Its own dbt tests, the ones that already gate
`dbt build`, are published as ODCS data contracts in OpenMetadata rather than
redefined a second time for governance, one definition of quality, not two.
Data-quality validation with Great Expectations is on the roadmap, not yet built.

It runs against a **published release**, never a checkout. That is the point:
this repository is a *consumer*. It has no access to the emulator's source, so
anything that works here works for anyone.

```sh
git clone https://github.com/calvinchengx/fabric-platform-notebook-pipelines
cd fabric-platform-notebook-pipelines
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

The full stack is **17 services** — four emulators (entra, keyvault, arm,
fabric), Sail, a Spark agent, a SQL Server sidecar, the source systems (three
mokapi instances, Postgres, Redpanda, Debezium) and OpenMetadata with its own
Postgres, OpenSearch and a one-shot migration. Budget **~8 GB** to Docker;
OpenSearch alone asks for a 1 GB heap.

**Why ARM is in the stack.** A Fabric **capacity** is an Azure resource
(`Microsoft.Fabric/capacities`), created through `management.azure.com` and only
then visible on the Fabric control plane. Nothing in the Fabric REST API makes
one. So [arm-emulator](https://github.com/calvinchengx/arm-emulator) is here to
be the thing that does, and `make verify` runs the real sequence: create the
capacity in ARM, wait for Fabric to see it, then create the workspace **on**
it.

That is a difference this platform used to tolerate rather than fix. The
emulator seeds a capacity and attaches it to every new workspace; real Fabric
does not, so the assertion "the workspace has a capacity" was true locally and
false in production, and it sat behind a `capacity_is_auto_assigned` flag.
Creating the resource properly retires the flag: **"this workspace runs on a
capacity" is now asserted on both targets.**

**The capacity is chosen at create, and never changed afterwards.** An existing
workspace already carries the capacity someone put it on, and the platform
adopts it. That is why `FABRIC_CAPACITY` is *optional*: a real run against an
established workspace needs no capacity configuration, because Fabric already
knows. It is read only when a workspace has to be created, since `capacityId`
is optional on `POST /v1/workspaces` and a workspace created without one has no
capacity at all, which no Lakehouse can live in.

Against real Fabric no capacity is ever created, and none is ever reassigned.
Creating one is billable infrastructure; moving a live workspace onto a
different one changes its billing and disturbs whatever is running on it.
Both rules have tests.

[azure-apim-emulator](https://github.com/calvinchengx/azure-apim-emulator) is
absent for a simpler reason: **nothing here sits behind a gateway.** APIM is the
publish-and-front-an-API product, and the four vendor systems are consumed
directly, three from mokapi over REST and one over CDC. A real enterprise might
well put those vendor APIs behind APIM and enforce subscription keys or
`validate-jwt` there, but this platform does not, so adding the service would
mean inventing a hop the code does not make.

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

The four vendors serve ~194 MB of seeded export, it lands in OneLake
byte-identical, bronze and silver are computed by a **Fabric notebook** on
Spark, gold builds in the Warehouse via **dbt-fabric** with 60 data tests, and
the semantic model answers DAX over the Power BI `executeQueries` wire.

**No PR check runs a platform step.** `acceptance.yml` fires on a
`repository_dispatch` from fabric-emulator's release workflow, on a daily
schedule, and on `workflow_dispatch` — never on `pull_request`. That is
deliberate (the full stack is too heavy for a PR gate) and it has a cost worth
knowing: a change that breaks a platform step reaches `main` and is caught by
the next scheduled run rather than before the merge. Read the acceptance run,
not the PR checks, before trusting a green `main`.

The reporting pack is the point of the whole thing: revenue in USD by
**financial year** (Contoso's starts 1 April), by **product segment** and by
**customer segment**, across both selling systems — 22,000 people resolved
between them.

**XMLA is no longer the named gap.** It was, for most of this repository's
life: `make verify` ran a real ADOMD.NET client against the model and reported
`no endpoint`, reported rather than skipped, because a surface nobody asks
about is a surface nobody hears about when it breaks.

fabric-emulator shipped it. `docs/24` there now records the read path as
delivered and the write path with it, driven in CI by two of Microsoft's own
clients — `sempy` over the DMV rowsets and `semantic-link-labs` through the
Tabular Object Model — both reading the same model. The probe was built to
notice on its own: when XMLA answers it asserts the total equals the one REST
returned, and two independent surfaces agreeing on one number is a stronger
statement than either alone.

What is still absent there, and so still absent here: **MDX, `<Refresh>`, the
LRO continuation byte, and structural writes** (new tables, columns,
relationships). Measures, lineage tags and annotations on existing objects are
in scope.

## License

Apache-2.0. This repository is a **consumer**: it holds no emulator source and
runs against a published release, so what works here works for anyone.
