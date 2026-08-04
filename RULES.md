# Rules for this codebase

This platform runs against **real Microsoft Fabric**. `fabric-emulator` is one
target it can be pointed at — not the thing it is built for. Every rule below
exists to keep that true, because the failure mode is silent: emulator-shaped
code passes every local test and breaks the day it meets production.

Each rule names the test that enforces it. **A rule with no test is a rule we
will break** — the "enforced by" column is the honest part of this document, and
`judgement` means exactly that.

---

## 1. Fabric, not the emulator

| | |
|---|---|
| **Rule** | Every difference between the emulator and real Fabric lives in `platform/target.py`, selected by `FABRIC_TARGET=emulator\|real`. Nowhere else. |
| **Why** | A localhost URL or a seeded credential anywhere else is a workaround that ships to production — and the emulator goes on passing, so nothing reveals it. |
| **Enforced by** | `test_the_emulator_appears_only_in_the_target_resolver` |

| | |
|---|---|
| **Rule** | TLS verification is never hardcoded off. The real target verifies, and no configuration can disable it. |
| **Why** | `S.verify = False` was written once as a local convenience. Against production it is a security defect. |
| **Enforced by** | `test_tls_verification_is_never_hardcoded_off` |

| | |
|---|---|
| **Rule** | Never assert on emulator conveniences. |
| **Why** | `assert ws.get("capacityId")` passed locally and would have failed on real Fabric, which does not auto-assign a capacity. The assertion is now behind `T.capacity_is_auto_assigned`. |
| **Enforced by** | judgement — ask "is this true on both targets?" of every assertion about platform behaviour |

| | |
|---|---|
| **Rule** | Credentials come from **Key Vault** — `azure-keyvault-emulator` locally, the customer's real Azure Key Vault in production. Never from the source tree. |
| **Why** | A key in a repository has already leaked: it is in every clone, in the reflog, and in whatever CI cached the checkout. It also skips what a real deployment must get right — an identity permitted to read a vault, and rotation without a code change. Two structural exceptions: the **bootstrap** Entra credential (reading the vault needs it) in `target.py`, and `seed_secrets.py`, which exists only so a clone is self-contained and does not run against real vendors. |
| **Enforced by** | `test_credentials_come_from_key_vault` |

| | |
|---|---|
| **Rule** | The `FABRIC_TARGET` contract is **installed**, never restated. `platform/target.py` consumes the published `fabric-target` package and adds only this platform's own policy. |
| **Why** | It used to restate it, and the copy drifted: the real target resolved an Entra client-credentials flow and demanded `AZURE_CLIENT_SECRET`, so `az login` could not drive the platform, a managed identity could not, and it could not have run inside a Fabric notebook at all — there is no client secret to give there. The emulator never noticed, because it does not care which identity shows up. A copied contract is one you get wrong in the branch nothing exercises. |
| **Enforced by** | `test_the_toggle_contract_is_installed_not_restated` |

| | |
|---|---|
| **Rule** | Never import `common` from the fixture wheels. |
| **Why** | It is the emulator's own client plumbing. Using it would void the claim this repository exists to make: that a consumer can build against a published image without the source. |
| **Enforced by** | `test_the_emulator_client_plumbing_is_never_imported` |

## 2. Big data, not convenience

| | |
|---|---|
| **Rule** | `bronze` and `silver` run **in the engine**. No `duckdb`, `pandas`, `deltalake` or `pyarrow` in a transform. |
| **Why** | A client-side dataframe puts one machine in the data path and silently caps the platform at its memory. Spark reads `abfs://…` directly — the same code at 100,000 rows and at a hundred million. |
| **Enforced by** | `test_the_transforms_are_engine_side` |

| | |
|---|---|
| **Rule** | A transform's session comes from `spark.session()` in a script, and from the ambient `spark` in a notebook. Never `SparkSession.builder` at a call site. |
| **Why** | Inside a Fabric notebook a session is ambient with the workspace identity and the attached lakehouse; building a second one is wrong and slower. This is what makes the transforms paste-able into a notebook. |
| **Enforced by** | judgement |

| | |
|---|---|
| **Rule** | `silver` runs as a **Fabric Notebook** — published as a Notebook item, executed by a RunNotebook job — and its source is a real file, never a string assembled at publish time. |
| **Why** | Most Fabric data engineering is written in notebooks, and until this step existed nothing here exercised one: the transform was a Spark Connect script that merely resembled notebook code, so "the transforms are paste-able into a notebook" was an assertion rather than a result. The file matters as much as the job — a notebook built from an f-string is not Python to any tool, so ruff and ty go blind and a syntax error surfaces as a failed cell on a remote engine instead of at `make lint`. |
| **Enforced by** | `test_silver_runs_as_a_fabric_notebook` |

| | |
|---|---|
| **Rule** | The platform must prove it runs **unattended**: a schedule is created, and on a controllable clock its firing is asserted by `invokeType: "Scheduled"` — never merely by the schedule existing. `schedule` runs LAST, because advancing the clock moves what every job status derives from. |
| **Why** | Everything else here runs because someone typed `make verify`, which proves the platform works and not that it operates. Broken scheduling is silent in the worst way: the data is yesterday's and every row count, table and dashboard still looks correct. A schedule that exists is not a schedule that fires, and only `invokeType` separates a scheduled run from the manual one that already happened earlier in the same pipeline. |
| **Enforced by** | `test_the_platform_proves_it_runs_unattended`, `test_the_schedule_step_runs_last` |

| | |
|---|---|
| **Rule** | The **source system is a node**, not a filename. Each ingest step names the vendor as a Connection and reports the movement from it; `bronze` reports its landing→bronze hop so those nodes are not orphans. |
| **Why** | A medallion does not begin in Fabric — it begins at a vendor's API or a production database — but an edge used to need a (workspace, item, path) triple at both ends, so the graph could only start at a file already in `Files/landing/` and the system that put it there could not be named. A connection rather than a URI because it holds the credential, carries a display name, and is what the client actually authenticated through. bronze must report too: Spark reads `abfs://` directly, so the emulator sees bytes leave and bytes arrive with nothing tying them together, and without that hop the vendors float beside the medallion instead of feeding it. |
| **Enforced by** | `test_the_source_systems_are_named_in_lineage`, `test_the_landing_hop_is_reported_so_sources_are_not_orphans` |

| | |
|---|---|
| **Rule** | A lineage report uses the precise `moves` form — one `{reads, writes}` per derivation — never flat read/write lists. |
| **Why** | Flat lists cross-product. A step reading two feeds and writing two paths claims four movements where two happened, and the phantoms look exactly as plausible as the real edges. This repository shipped three such edges once already, from a declared read/write set on the silver notebook. |
| **Enforced by** | `test_lineage_reports_use_the_precise_move_form` |

| | |
|---|---|
| **Rule** | The platform must prove it **reacts to a delivery**: a file dropped at a marker path starts a run, evidenced by `invokeType: "EventTriggered"`. The trigger watches a dedicated marker, never the vendor's own landing prefix. |
| **Why** | A schedule answers "run at 02:00" and cannot answer "run when it lands" — for an external feed the export finishes when it finishes, and a fixed hour either reprocesses yesterday or processes nothing. The marker matters as much as the trigger: the POS export lands as 21 parts, so a prefix over them would start 21 refreshes of one delivery, each correct alone and the set of them nonsense. |
| **Enforced by** | `test_the_platform_proves_it_reacts_to_a_delivery`, `test_the_trigger_watches_a_marker_not_the_landing_zone` |

| | |
|---|---|
| **Rule** | Binding an event trigger is **emulator-only**, behind `T.event_triggers_have_rest_api`. Against real Fabric the step creates nothing and says so. |
| **Why** | This is the one asymmetry this platform cannot close. Real Fabric has no public REST for the binding — the Eventstream/Reflex rule is assembled in the portal by hand — so a deployment cannot declare it the way it declares a lakehouse or a schedule. Inventing a REST call that does not exist would be worse than the gap; everything downstream of the binding is ordinary Fabric and is exercised by the job it starts. |
| **Enforced by** | `test_the_platform_proves_it_reacts_to_a_delivery` |

| | |
|---|---|
| **Rule** | A clock advance must fit inside one token lifetime, and the clock must be put back on EVERY path — `finally`, so a failing run restores it too. Any job started in the advanced frame is polled to a terminal state before the reset. |
| **Why** | Only Fabric's clock moves; the Entra emulator that mints the tokens keeps its own. Jump further than a token lives and the two disagree permanently — every later call 401s `invalid token: expired`, including freshly minted tokens, because the new one is already past expiry as far as Fabric is concerned. It presents as an authentication fault and is really the clock lever. A stack left advanced breaks whatever runs next with an error nobody would trace back here. And resetting *under a running job* is its own fault: the job's start was stamped in the advanced frame, so its end lands in the old one and the instance reports having finished before it began. |
| **Enforced by** | `test_the_clock_advance_fits_inside_one_token_lifetime`, `test_the_schedule_step_puts_the_clock_back` |

| | |
|---|---|
| | |
|---|---|
| **Rule** | A step asserts the OUTCOME of what it started, never merely that it was created. A job is polled to a terminal state and its status checked. |
| **Why** | The schedule step asserted that a job instance with `invokeType=Scheduled` existed and stopped there. It logged "the platform runs unattended" over a run that had died mid-notebook on a Delta commit conflict, and `make verify` reported 14/14 across two such failures. A schedule that reliably starts something that reliably fails is not unattended operation; it is an alarm nobody wired up. The same shape appears wherever a create is mistaken for a result. |
| **Enforced by** | `test_the_schedule_step_asserts_the_run_SUCCEEDED` |

| **Rule** | The stack RUNS the notebook; this platform never plays the Spark pool. `compose` provides the published `spark-agent` and the emulator is given `FABRIC_SPARK_AGENT_URL`. |
| **Why** | A Fabric notebook is executed by a Spark pool that reports back, and the emulator mirrors that rather than completing a job on a clock. Until fabric-emulator 0.15.0 no published artifact could be that engine — the spark-agent image shipped without the agent in it — so this platform supplied one, in `platform/engine.py`. That driver existed only because of a packaging bug upstream, and it is gone. Supplying an engine again would mean this repository had stopped being a consumer. |
| **Enforced by** | `test_the_platform_does_not_supply_its_own_spark_pool` |

## 3. Layers do not leak

| | |
|---|---|
| **Rule** | `fabric.py` knows Fabric. `sources.py` knows vendors. Neither knows the other. |
| **Why** | A Fabric client that knows a vendor's DSN has merged two things that change for different reasons at different times. |
| **Enforced by** | `test_the_fabric_client_knows_nothing_about_the_source_systems` |

| | |
|---|---|
| **Rule** | Source systems are real infrastructure — mokapi over HTTP, Postgres + Debezium + Redpanda for CDC — never in-process function calls. |
| **Why** | Calling a generator in-process makes lineage start at a landed file, and the vendor is never a node. It also skips auth, transport and failure entirely. |
| **Enforced by** | `test_every_spec_has_a_serve_script`, `test_every_operation_requires_a_key` |

| | |
|---|---|
| **Rule** | Each source system gets its **own** mokapi instance, mounted only its own spec and its own bytes. Never one instance serving several vendors. |
| **Why** | Separate companies are separate processes. Sharing one makes a single vendor's outage everyone's outage, puts every payload under one memory ceiling — the shape that let a 170 MB body OOM-kill the container — and hands each vendor every other one's export to serve by a path typo. |
| **Enforced by** | `test_one_mokapi_instance_per_source` |

## 4. Numbers are claims

| | |
|---|---|
| **Rule** | Every step asserts its results against the fixture constants. A printed number is not a test. |
| **Why** | The whole platform is a verification harness. A step that logs `93,571` and asserts nothing is decoration. |
| **Enforced by** | judgement — but every step currently does |

| | |
|---|---|
| **Rule** | Assert against a **different** source than the one that produced the number. |
| **Why** | A query grading its own output confirms nothing. The CDC watermark is checked against `EXPECTED_ERP_CHANGE_EVENTS`, not against the rows we happened to read. |
| **Enforced by** | judgement |

| | |
|---|---|
| **Rule** | Gate on a condition, never a sleep. |
| **Why** | A fixed wait passes on a fast machine, fails on a loaded one, and — worst — passes with a partial stream, landing a shorter file that any count stated as a minimum accepts. |
| **Enforced by** | judgement |

## 5. Reproducibility

| | |
|---|---|
| **Rule** | Every image is pinned in `versions.env`. Never `latest`. Every pin is substituted by a compose file. |
| **Why** | `latest` makes a green run unattributable: something worked, but not which release. A pin nothing uses is a comment. |
| **Enforced by** | `test_every_image_is_pinned_to_a_version`, `test_compose_reads_every_pin` |

| | |
|---|---|
| | |
|---|---|
| **Rule** | The release pins move only in a run that verified them. The acceptance run adopts the dispatched version itself, after `make verify` passes; nobody hand-edits `FABRIC_EMULATOR_VERSION`. |
| **Why** | The pin is this repository's claim about which release carries a working platform. Typed by hand it is an intention; written by the run that exercised it, it is a result. The gate is the whole argument — an `if: always()` on the adopt step would move the pin exactly when the evidence says not to. |
| **Enforced by** | `test_the_pin_moves_only_after_a_green_verify`, `test_the_acceptance_run_uses_the_dispatched_version` |

| | |
|---|---|
| | |
|---|---|
| **Rule** | `tests/test_repo.py` must not import `fabric` or `target`. A runtime guard that needs testing there goes in a dependency-free module, like `platform/apipath.py`. |
| **Why** | That file's first paragraph promises its tests need no emulator, no Docker and no fixture wheels — they are the part of CI green from day one on all three platforms. Importing the client resolves a target, which needs the `fabric-target` wheel `make fixtures` installs from the pinned release, deliberately outside `uv.lock`. The day a guard was tested by importing the client that carries it, CI went red on ubuntu, macOS and Windows at once. |
| **Enforced by** | `test_the_repo_tests_need_no_fixture_wheels` |

| **Rule** | uv, strictly. `pyproject.toml` + committed `uv.lock`. No bare `python`, no `pip`, no `--with`. |
| **Why** | `--with pytest` resolves fresh every run, so the same commit can test against two different suites. |
| **Enforced by** | `test_python_is_only_ever_invoked_through_uv`, `test_the_lockfile_is_committed` |

| | |
|---|---|
| **Rule** | The fixture generators come from the pinned release, outside the lock. Re-run `make fixtures` after any `uv sync` — measured: `uv run --frozen` preserves them, `uv sync` prunes them. |
| **Why** | *Which release they came from* is the thing under test; pinning them in `uv.lock` would defeat the entire repository. |
| **Enforced by** | `pipeline.preflight()` fails with the fix, not an ImportError six steps in |

## 6. Three platforms, one command

| | |
|---|---|
| **Rule** | Every Makefile recipe must survive `cmd.exe`. No pipes, `rm`, backticks, `&&`, `if`, `$(shell …)`. Logic goes in `scripts/`, which is Python. |
| **Why** | GNU Make on Windows runs recipes through `cmd.exe`. A pipe in a recipe is a bug that only ever bites the Windows user. |
| **Enforced by** | `test_every_make_recipe_survives_cmd_exe` |

| | |
|---|---|
| **Rule** | Do not bind well-known host ports. |
| **Why** | A native Postgres service won the race against this stack's container and produced `role "contoso" does not exist` — an error naming neither the port nor the process. Source ports are 18090/18081/18083/19092/55432. |
| **Enforced by** | judgement |

---

## What lives in `fabric-emulator` instead

Nothing here duplicates the emulator's own guidance. Two things belong there:

- **`docs/21-real-fabric-toggle.md`** and the `fabric-target` package that
  implements it — the `FABRIC_TARGET` contract. It **is** published now, beside
  the fixture wheels, and `make fixtures` installs it. What survives in our
  `platform/target.py` is only the consumer half: the decisions that are this
  platform's policy rather than the toggle's (who plays the Spark pool, whether
  a capacity must be assigned, OneLake's local Host header). Endpoints,
  credentials and the seed guards come from the package. Anything you are
  tempted to add to `target.py` that would be true for *any* consumer belongs
  upstream instead.
- Anything that exists *only* to make the emulator work. The dbt runner image is
  **not** that: `dbt-fabric` + ODBC Driver 18 is Microsoft's own requirement
  against real Fabric, so it stays here as this platform's build tooling.

The test for where something belongs: **does it exist only because of the
emulator?** If yes, upstream. If it would be needed against production too, here.
