"""A thin fabric-emulator client, written from the published quickstart.

DELIBERATELY NOT `common.py`. That module ships inside the contoso-fixtures
wheel and would give this repository the emulator's own client plumbing — which
would quietly void the single claim this repository exists to make: that a
consumer can build against a PUBLISHED image without the source. A test in
tests/test_repo.py enforces the absence.

Everything here comes from docs/01-quickstart: the seeded daemon principal, the
two audiences, the control-plane routes, and OneLake's ADLS Gen2 create/append/
flush. If a call here needs something the docs do not state, that is a finding
about the docs and should be reported as one rather than worked around.
"""

from __future__ import annotations

import os
import pathlib
import ssl
import urllib.parse

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT_ID = "cccccccc-0000-0000-0000-000000000002"
CLIENT_SECRET = "daemon-app-secret"  # seeded dev value, published in the docs

ENTRA = os.environ.get("ENTRA_URL", "https://localhost:8443")
FABRIC = os.environ.get("FABRIC_URL", "https://localhost:9443")
POS_API = os.environ.get("POS_API_URL", "http://localhost:18090")

FABRIC_AUD = "https://api.fabric.microsoft.com"
STORAGE_AUD = "https://storage.azure.com"

# OneLake is Host-routed at onelake.dfs.fabric.microsoft.com. curl does that with
# --resolve; requests does it by addressing the emulator and setting Host, which
# is the same trick and keeps the URL honest about who is being addressed.
ONELAKE_HOST = "onelake.dfs.fabric.microsoft.com"

ERP_DSN = os.environ.get(
    "ERP_DSN", "postgresql://contoso:contoso-erp-dev@localhost:55432/erp"
)
DEBEZIUM = os.environ.get("DEBEZIUM_URL", "http://localhost:18083")
REDPANDA = os.environ.get("REDPANDA_BOOTSTRAP", "localhost:19092")

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / "state.json"

# The emulator serves a self-signed certificate. Verification is off rather than
# pinned because a consumer following the quickstart has no CA to pin to; the
# TLS path itself is still exercised.
S = requests.Session()
S.verify = False


def log(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def token(audience: str) -> str:
    """Client credentials against the seeded daemon app (quickstart §2)."""
    r = S.post(
        f"{ENTRA}/{TENANT}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
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
    """ADLS Gen2 against OneLake, Host-routed (quickstart §5)."""
    return S.request(
        method,
        f"{FABRIC}/{path.lstrip('/')}",
        headers={
            "Authorization": f"Bearer {tok}",
            "Host": ONELAKE_HOST,
            **kw.pop("headers", {}),
        },
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
