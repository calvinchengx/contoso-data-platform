"""The pinned release, and the URLs that follow from it.

`versions.env` is the ONLY place a version is written. docker compose reads it
directly via `--env-file`, and everything in Python asks this module, so the
pin cannot be stated twice and drift — which is the failure this whole
repository exists to catch one level up.

That single point is also what lets an acceptance run verify a release that has
only just shipped: `set_release.py` rewrites the two versions the emulator's
own workflow tags, and the summary then reports the version actually tested
rather than the one the repo happened to be pinned to.
"""

import pathlib
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO = "calvinchengx/fabric-emulator"

# All three pins. The family ships on independent cadences, so one string
# cannot describe the stack — see the file's own comment.
VERSIONS = ROOT / "versions.env"

# The generators. Published from the emulator's release workflow so that this
# repo's assertions and the in-tree examples' assertions come from ONE seeded
# generator — see scripts/build_fixture_wheels.py over there.
WHEELS = ["contoso_fixtures", "contoso_fixtures_advanced"]


def pins() -> dict[str, str]:
    """Every pinned image version, by the variable name compose uses."""
    out = {}
    for line in VERSIONS.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def version() -> str:
    """The fabric-emulator release under test — the subject of this repo."""
    return pins()["FABRIC_EMULATOR_VERSION"]


def tag():
    return "v" + version()


def wheel_urls(v=None):
    v = v or version()
    return [
        f"https://github.com/{REPO}/releases/download/v{v}/{name}-{v}-py3-none-any.whl"
        for name in WHEELS
    ]


def published(url, timeout=15):
    """True if the asset exists. GitHub redirects release assets, so a plain
    HEAD that follows redirects is the honest check — a 404 page returned with
    status 200 would otherwise read as success."""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except urllib.error.HTTPError:
        return False
    except OSError:
        return None  # no network — say so rather than claim absence


def wheels_published(v=None):
    """(all_present, per_url_status). None anywhere means 'could not tell'."""
    results = {u: published(u) for u in wheel_urls(v)}
    if any(s is None for s in results.values()):
        return None, results
    return all(results.values()), results
