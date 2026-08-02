"""The pinned release, and the URLs that follow from it.

One file reads `.emulator-version`. Everything else asks this module, so the
pin cannot be stated in two places and drift — which is the failure this whole
repository exists to catch one level up.
"""
import pathlib
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO = "calvinchengx/fabric-emulator"

# The generators. Published from the emulator's release workflow so that this
# repo's assertions and the in-tree examples' assertions come from ONE seeded
# generator — see scripts/build_fixture_wheels.py over there.
WHEELS = ["contoso_fixtures", "contoso_fixtures_advanced"]


def version():
    return (ROOT / ".emulator-version").read_text().strip()


def tag():
    return "v" + version()


def wheel_urls(v=None):
    v = v or version()
    return [f"https://github.com/{REPO}/releases/download/v{v}/"
            f"{name}-{v}-py3-none-any.whl" for name in WHEELS]


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
