"""A Fabric client. Not an emulator client that happens to reach Fabric.

Every call below is the real Fabric contract — the Entra client-credentials
flow, the `/v1` control plane, OneLake's ADLS Gen2 create/append/flush. What
differs between the local family and production is resolved in target.py and
appears here only as configuration: an endpoint, a credential, a TLS flag, one
Host header. Nothing in this module is shaped around the emulator.

DELIBERATELY NOT `common.py`. That module ships inside the contoso-fixtures
wheel and would give this repository the emulator's own client plumbing — which
would quietly void the single claim this repository exists to make: that a
consumer can build against a PUBLISHED image without the source. A test in
tests/test_repo.py enforces the absence.
"""

from __future__ import annotations

import os
import pathlib
import ssl
import urllib.parse

import requests
import target
import urllib3

T = target.resolve()

# Real Fabric audiences, on both targets — the emulator validates the same ones.
FABRIC_AUD = "https://api.fabric.microsoft.com"
STORAGE_AUD = "https://storage.azure.com"

FABRIC = T.api_root
POS_API = os.environ.get("POS_API_URL", "http://localhost:18090")

ERP_DSN = os.environ.get(
    "ERP_DSN", "postgresql://contoso:contoso-erp-dev@localhost:55432/erp"
)
DEBEZIUM = os.environ.get("DEBEZIUM_URL", "http://localhost:18083")
REDPANDA = os.environ.get("REDPANDA_BOOTSTRAP", "localhost:19092")

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / "state.json"

S = requests.Session()
# TLS verification follows the TARGET, never a constant. Against real Fabric it
# is on and cannot be turned off from configuration; the local family serves
# self-signed certificates a consumer has no CA for.
S.verify = T.verify_tls
if not T.verify_tls:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def log(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def token(audience: str) -> str:
    """Entra client credentials. The same flow and the same audiences against
    both targets — only the authority and the principal differ."""
    r = S.post(
        f"{T.authority}/{T.tenant}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": T.client_id,
            "client_secret": T.client_secret,
            "scope": f"{audience}/.default",
        },
        timeout=30,
    )
    r.raise_for_status()
    tok = r.json()["access_token"]
    assert tok, "entra returned an empty access token"
    return tok


def fabric(method: str, path: str, tok: str, **kw):
    r = S.request(
        method,
        f"{FABRIC}/v1{path}",
        headers={"Authorization": f"Bearer {tok}", **kw.pop("headers", {})},
        timeout=120,
        **kw,
    )
    return r


def onelake(method: str, path: str, tok: str, **kw):
    """ADLS Gen2 against OneLake.

    Real Fabric has its own hostname and is addressed directly. The emulator
    serves OneLake on the Fabric port and routes by Host header — the same
    thing `curl --resolve` does — so that override is applied only when the
    target asks for it.
    """
    headers = {"Authorization": f"Bearer {tok}", **kw.pop("headers", {})}
    if T.onelake_host_header:
        headers["Host"] = T.onelake_host_header
    return S.request(
        method,
        f"{T.onelake_url}/{path.lstrip('/')}",
        headers=headers,
        timeout=300,
        **kw,
    )


def upload(workspace: str, item: str, rel_path: str, blob: bytes, tok: str) -> int:
    """create → append → flush, the three-step ADLS Gen2 write.

    Chunked because that is how a real ADLS client writes a 170 MB export, and
    because a single append would say nothing about whether `position` is
    handled correctly across calls.
    """
    quoted = urllib.parse.quote(rel_path)
    base = f"/{workspace}/{item}/{quoted}"

    r = onelake("PUT", f"{base}?resource=file", tok)
    assert r.status_code in (201, 202), (r.status_code, r.text[:200])

    chunk = 8 * 1024 * 1024
    pos = 0
    while pos < len(blob):
        part = blob[pos : pos + chunk]
        r = onelake("PATCH", f"{base}?action=append&position={pos}", tok, data=part)
        assert r.status_code in (200, 202), (pos, r.status_code, r.text[:200])
        pos += len(part)

    r = onelake("PATCH", f"{base}?action=flush&position={pos}", tok)
    assert r.status_code in (200, 202), (r.status_code, r.text[:200])
    return pos


def server_cert_pem(url: str) -> str:
    """The certificate the stack is actually presenting.

    Tools that own their own HTTP client (dbt, ODBC) verify the chain and have
    no CA for a self-signed cert; handing them exactly what is being served
    keeps TLS exercised rather than disabled globally.
    """
    host, _, port = url.split("://", 1)[1].partition(":")
    return ssl.get_server_certificate((host, int(port or 443)))
