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
| **Rule** | The session comes from `spark.session()`, never `SparkSession.builder` at a call site. |
| **Why** | Inside a Fabric notebook a session is ambient with the workspace identity attached; building a second one is wrong and slower. This is what makes the transforms paste-able into a notebook. |
| **Enforced by** | judgement |

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

- **`docs/21-real-fabric-toggle.md`** — the `FABRIC_TARGET` contract this repo
  implements. `python/fabric-target/` implements it too but is **not published**,
  so a consumer must restate it; publishing it beside the fixture wheels would
  delete our `target.py`.
- Anything that exists *only* to make the emulator work. The dbt runner image is
  **not** that: `dbt-fabric` + ODBC Driver 18 is Microsoft's own requirement
  against real Fabric, so it stays here as this platform's build tooling.

The test for where something belongs: **does it exist only because of the
emulator?** If yes, upstream. If it would be needed against production too, here.
