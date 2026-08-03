"""One switch between the emulator and real Fabric: `FABRIC_TARGET`.

THIS PLATFORM IS FABRIC CODE. It is not emulator code that happens to run on
Fabric. Everything the two targets genuinely differ about is resolved here and
nowhere else, so a reader can see the whole difference in one file — and so
that "would this run on real Fabric?" is answerable by reading it rather than
by auditing every call site.

THE CONTRACT ITSELF IS NOT WRITTEN HERE. It is `fabric-target`, published from
the emulator's release workflow and installed by `make fixtures` alongside the
generators. This file is the CONSUMER half: the handful of decisions that are
this platform's policy rather than the toggle's — who plays the Spark pool,
whether a capacity has to be assigned, which Host header OneLake wants locally.

That split is new, and it is the fix for a real defect. While `fabric-target`
was unpublished this file RESTATED the contract, and the restatement drifted:
it resolved the real target to an Entra client-credentials flow and required
AZURE_CLIENT_SECRET, which meant `az login` did not work, a managed identity
did not work, and the platform could not have run inside a Fabric notebook at
all — a notebook has no client secret to give. The published package resolves
`DefaultAzureCredential` instead: env service-principal vars win when set, and
otherwise the developer's own `az login` does. A contract you copy is a
contract you get wrong, so this file no longer copies one.

Ids can never match across targets, so anything durable is addressed BY NAME
and resolved to a GUID per target.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import fabric_target


class AccessToken(Protocol):
    token: str


class TokenCredential(Protocol):
    """azure-core's credential shape, structurally.

    Declared rather than imported so this module keeps working when
    azure-identity is not installed — against the emulator it is not, and the
    credential is `fabric-target`'s own stdlib client-credentials object. What
    matters is the shape both satisfy, which is the reason a Fabric notebook's
    managed identity drops in here without anything above knowing.
    """

    def get_token(self, *scopes: str) -> AccessToken: ...


EMULATOR = "emulator"
REAL = "real"

# The one workspace this platform owns. Named here because the name is the
# cross-target address — `provision.py` resolves it to a GUID per target — and
# because real mode is workspace-scoped by construction: `fabric-target`
# refuses to operate tenant-wide, which is the right rule and one this platform
# has no reason to opt out of.
WORKSPACE = "contoso-analytics"


def target() -> str:
    t = os.environ.get("FABRIC_TARGET", EMULATOR).lower()
    if t not in (EMULATOR, REAL):
        raise SystemExit(f"FABRIC_TARGET must be {EMULATOR!r} or {REAL!r}, got {t!r}")
    return t


@dataclass(frozen=True)
class Target:
    name: str
    api_root: str
    tenant: str
    # A TokenCredential — `get_token(scope) -> .token`. Against the emulator it
    # is the seeded daemon; against real Fabric, DefaultAzureCredential, which
    # is what makes `az login` and a Fabric notebook's own managed identity
    # work without this platform knowing which one it got.
    credential: TokenCredential
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
    # WHO EXECUTES A NOTEBOOK. Real Fabric runs a RunNotebook job on its own
    # Spark pool: the client submits and polls, and nothing else is required of
    # it. The emulator parses the notebook into ordered cells and records a
    # Pending run, then waits for an attached engine to execute them and report
    # back — deliberately, so that a terminal job status means execution really
    # happened rather than that a clock advanced.
    #
    # So the notebook itself is identical on both targets, and this is the only
    # difference: on the emulator the platform must ALSO play the Spark pool.
    # That is emulator scaffolding, which is why it is selected here and why
    # engine.py never runs against production.
    runs_notebooks_itself: bool
    # Where secrets live. The azure-keyvault-emulator locally, the customer's
    # real vault in production — never the source tree, on either target.
    vault_url: str
    # Real Entra knows Azure SQL and Power BI as first-party resources, so a
    # token for those audiences needs no setup. The emulator's entra mints only
    # for audiences it has been told about, and exposes an admin API to do it.
    # None on the real target means "nothing to register".
    entra_admin_api: str | None

    @property
    def is_emulator(self) -> bool:
        return self.name == EMULATOR

    def token(self, audience: str) -> str:
        """A bearer token for `audience`, from whatever credential this target
        resolved. The audiences are the real Fabric ones on both targets — the
        emulator validates the same strings — so nothing above this line knows
        which identity answered, which is the whole point: `az login`, a
        service principal and a notebook's managed identity are all just a
        credential here.
        """
        return self.credential.get_token(f"{audience}/.default").token

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


def resolve() -> Target:
    # Declared before the resolver runs, because real mode refuses to construct
    # without a workspace scope. An explicit default is not a workaround for
    # that rule — this platform genuinely owns one workspace, and saying so is
    # what the rule asks for. An operator pointing the platform at a
    # differently named workspace sets FABRIC_WORKSPACE and this leaves it be.
    os.environ.setdefault("FABRIC_WORKSPACE", WORKSPACE)

    # Endpoints, credentials and the seed guards come from the PUBLISHED
    # contract; everything below is this platform's own policy.
    ft = fabric_target.target(target(), fresh=True)
    # `fabric-target` hands back the versioned control plane; this platform
    # composes `/v1` per call, and xmla_probe needs the bare host.
    api_root = ft.api_root.removesuffix("/v1")

    if ft.is_real:
        vault = ft.vault_url
        if not vault:
            raise SystemExit(
                "FABRIC_TARGET=real needs AZURE_KEY_VAULT_URL — secrets come "
                "from the customer's own Key Vault, never the source tree."
            )
        return Target(
            name=REAL,
            api_root=api_root,
            tenant=ft.tenant,
            credential=ft.credential,
            onelake_url=ft.onelake_url,
            onelake_host_header=None,
            verify_tls=True,
            capacity_is_auto_assigned=False,
            # A Fabric Spark notebook supplies the session; nothing to connect to.
            spark_remote=os.environ.get("SPARK_REMOTE"),
            runs_notebooks_itself=True,
            vault_url=vault,
            entra_admin_api=None,
        )

    return Target(
        name=EMULATOR,
        api_root=api_root,
        tenant=ft.tenant,
        credential=ft.credential,
        onelake_url=ft.onelake_url,
        onelake_host_header="onelake.dfs.fabric.microsoft.com",
        # Off ONLY here, and not by this file's choice — the published contract
        # sets tls_verify False for the emulator, whose family serves
        # self-signed certificates a consumer has no CA for. The TLS path is
        # still exercised. On the real target it is True above, literally, and
        # no configuration reaches it.
        verify_tls=ft.tls_verify,
        capacity_is_auto_assigned=True,
        spark_remote=os.environ.get("SPARK_REMOTE", "sc://localhost:50051"),
        runs_notebooks_itself=False,
        vault_url=ft.vault_url,
        entra_admin_api=ft.entra_url + "/admin/api/apps",
    )
