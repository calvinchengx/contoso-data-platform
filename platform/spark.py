"""The Spark session, wherever this runs.

WHY SPARK AND NOT DUCKDB. bronze and silver are the distributed half of the
medallion. A single-node engine is the right tool for a Fabric *Python*
notebook and the wrong one for a platform that has to scale — so the transforms
are Spark, and the same code is a Spark notebook or a Spark Job Definition in
production.

WHERE THE SESSION COMES FROM. Inside a Fabric notebook `spark` already exists;
the platform must not build a second one. Outside a notebook — a laptop, CI, a
container — you connect to a Spark Connect endpoint. Both are the same
`SparkSession` afterwards, so nothing downstream knows the difference.

`pyspark-client` is the thin Connect client: about a megabyte, no JVM. The
engine is Sail here and a Fabric Spark pool in production.
"""

from __future__ import annotations

from fabric import T
from pyspark.sql import SparkSession


def session() -> SparkSession:
    # An ambient session wins. In a Fabric notebook one is already running with
    # the workspace identity and the default lakehouse attached, and connecting
    # somewhere else from inside it would be both wrong and slower.
    active = SparkSession.getActiveSession()
    if active is not None:
        return active

    if not T.spark_remote:
        raise SystemExit(
            "no Spark session and no SPARK_REMOTE. Inside a Fabric notebook a "
            "session is ambient; outside one, point SPARK_REMOTE at a Spark "
            "Connect endpoint."
        )
    return SparkSession.builder.remote(T.spark_remote).getOrCreate()


def lakehouse_uri(workspace: str, lakehouse: str) -> str:
    """The OneLake path Spark reads and writes.

    The real Fabric scheme on both targets — `abfs://{workspace}@onelake…` —
    because it is the engine that resolves it, not this process. Sail is
    configured with the emulator's storage endpoint and reaches the same store.
    """
    return f"abfs://{workspace}@onelake.dfs.fabric.microsoft.com/{lakehouse}"
