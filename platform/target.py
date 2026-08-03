"""One switch between the emulator and real Fabric: `FABRIC_TARGET`.

THIS PLATFORM IS FABRIC CODE. It is not emulator code that happens to run on
Fabric. Everything the two targets genuinely differ about is resolved here and
nowhere else, so a reader can see the whole difference in one file — and so
that "would this run on real Fabric?" is answerable by reading it rather than
by auditing every call site.

The contract is the emulator's own (docs/21-real-fabric-toggle):

    FABRIC_TARGET=emulator   local family, seeded credentials, self-signed TLS
    FABRIC_TARGET=real       api.fabric.microsoft.com, AZURE_* credentials, TLS on

Ids can never match across targets, so anything durable is addressed BY NAME
and resolved to a GUID per target.

NOTE FOR THE EMULATOR PROJECT: `python/fabric-target/` implements this contract
already, but it is not published, so a consumer cannot install it and must
restate the contract here. Publishing it alongside the fixture wheels would
remove this file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

EMULATOR = "emulator"
REAL = "real"


def target() -> str:
    t = os.environ.get("FABRIC_TARGET", EMULATOR).lower()
    if t not in (EMULATOR, REAL):
        raise SystemExit(f"FABRIC_TARGET must be {EMULATOR!r} or {REAL!r}, got {t!r}")
    return t


@dataclass(frozen=True)
class Target:
    name: str
    api_root: str
    authority: str
    tenant: str
    client_id: str
    client_secret: str
    onelake_url: str
    # The emulator serves OneLake on the Fabric port and routes by Host header,
    # the way `curl --resolve` does. Real Fabric has its own hostname and needs
    # no override — so this is the one place that difference lives.
    onelake_host_header: str | None
    verify_tls: bool
    # Real Fabric requires a capacity to exist and be assigned; the emulator
    # seeds one and auto-assigns it. Asserting the emulator's convenience would
    # fail against production for a reason that has nothing to do with the code
    # under test.
    capacity_is_auto_assigned: bool
    # Where the Spark session comes from. In a Fabric notebook `spark` is
    # ambient and this is None; outside one, a Spark Connect endpoint.
    spark_remote: str | None
    # Where secrets live. The azure-keyvault-emulator locally, the customer's
    # real vault in production — never the source tree, on either target.
    vault_url: str

    @property
    def is_emulator(self) -> bool:
        return self.name == EMULATOR

    def delta_storage_options(self, tok: str) -> dict[str, str]:
        """What delta-rs needs to reach OneLake on this target.

        Kept here rather than at the call site so that every difference between
        the emulator and real Fabric is in ONE file — which is what makes
        "would this run on real Fabric?" answerable by reading, instead of by
        auditing each module.
        """
        opts = {
            # The account name is the literal `onelake` on both targets.
            "azure_storage_account_name": "onelake",
            "azure_storage_token": tok,
            "azure_endpoint": f"{self.onelake_url}/onelake"
            if self.is_emulator
            else self.onelake_url,
        }
        if not self.verify_tls:
            # object_store verifies by default and fails with `invalid peer
            # certificate: UnknownIssuer` — naming neither the emulator nor the
            # fix. Never set on the real target.
            opts["allow_invalid_certificates"] = "true"
        return opts


def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise SystemExit(
            f"FABRIC_TARGET=real needs {name}. The real target authenticates with "
            f"a Microsoft Entra service principal — see docs/21-real-fabric-toggle."
        )
    return v


def resolve() -> Target:
    if target() == REAL:
        tenant = _require("AZURE_TENANT_ID")
        return Target(
            name=REAL,
            api_root=os.environ.get(
                "FABRIC_API_ROOT_URL", "https://api.fabric.microsoft.com"
            ),
            authority="https://login.microsoftonline.com",
            tenant=tenant,
            client_id=_require("AZURE_CLIENT_ID"),
            client_secret=_require("AZURE_CLIENT_SECRET"),
            onelake_url="https://onelake.dfs.fabric.microsoft.com",
            onelake_host_header=None,
            verify_tls=True,
            capacity_is_auto_assigned=False,
            # A Fabric Spark notebook supplies the session; nothing to connect to.
            spark_remote=os.environ.get("SPARK_REMOTE"),
            vault_url=_require("AZURE_KEY_VAULT_URL"),
        )

    fabric = os.environ.get("FABRIC_URL", "https://localhost:9443")
    return Target(
        name=EMULATOR,
        api_root=fabric,
        authority=os.environ.get("ENTRA_URL", "https://localhost:8443"),
        # The seeded tenant and daemon principal, published in the quickstart.
        tenant=os.environ.get(
            "AZURE_TENANT_ID", "11111111-1111-1111-1111-111111111111"
        ),
        client_id=os.environ.get(
            "AZURE_CLIENT_ID", "cccccccc-0000-0000-0000-000000000002"
        ),
        client_secret=os.environ.get("AZURE_CLIENT_SECRET", "daemon-app-secret"),
        onelake_url=fabric,
        onelake_host_header="onelake.dfs.fabric.microsoft.com",
        # Off ONLY here. The family serves self-signed certificates a consumer
        # has no CA for; the TLS path is still exercised. On the real target
        # this is True and there is no way to turn it off from configuration,
        # which is deliberate.
        verify_tls=False,
        capacity_is_auto_assigned=True,
        spark_remote=os.environ.get("SPARK_REMOTE", "sc://localhost:50051"),
        vault_url=os.environ.get("AZURE_KEY_VAULT_URL", "https://localhost:8444"),
    )
