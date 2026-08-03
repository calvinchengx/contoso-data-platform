"""Delta on OneLake, without a JVM or a Spark container.

The emulator serves OneLake over a **Blob dialect** as well as DFS, and that is
the surface Rust `object_store` — and therefore delta-rs — speaks
(docs/08-onelake). So a consumer can read and write real Delta tables with a
Python wheel and nothing else.

That is not a shortcut taken to save effort. This repository has to run on
Windows, macOS and Linux from `git clone` and `make`, and adding a Spark
container to reach a Delta table would put a JVM, a matching Python, and a
Spark Connect handshake between a reader and their first table.

TLS follows the TARGET. Against real Fabric object_store verifies normally;
against the local family it would fail with `invalid peer certificate:
UnknownIssuer` — a message naming neither the emulator nor the fix — so the
relaxation is applied there and only there.
"""

from __future__ import annotations

import pyarrow as pa
from deltalake import DeltaTable, write_deltalake
from fabric import STORAGE_AUD, T, token


def storage_options(tok: str | None = None) -> dict[str, str]:
    return T.delta_storage_options(tok or token(STORAGE_AUD))


def table_uri(workspace: str, item: str, name: str) -> str:
    return f"az://{workspace}/{item}/Tables/{name}"


def write(workspace: str, item: str, name: str, table: pa.Table, tok=None) -> int:
    write_deltalake(
        table_uri(workspace, item, name),
        table,
        storage_options=storage_options(tok),
        mode="overwrite",
        # The schema is overwritten too. Every step here is a full refresh, so a
        # table whose shape changed between runs must follow — otherwise the
        # write fails with `number of fields does not match`, which reads as a
        # data error rather than "the previous run wrote a different table".
        schema_mode="overwrite",
    )
    return table.num_rows


def read(workspace: str, item: str, name: str, tok=None) -> pa.Table:
    dt = DeltaTable(
        table_uri(workspace, item, name), storage_options=storage_options(tok)
    )
    return dt.to_pyarrow_table()
