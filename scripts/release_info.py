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
#
# `fabric_target` rides the same channel for the same reason, one level up: it
# is the emulator-or-real contract, and this repo used to RESTATE it in
# platform/target.py. The restatement drifted — it dropped the
# `DefaultAzureCredential` branch, so the real target demanded a client secret
# and could never have run inside a Fabric notebook, where there is no secret
# to give. A contract you copy is a contract you get wrong.
#
# The extras are not optional here: the package is stdlib-only at its core and
# lazily imports azure-identity (real credentials) and requests (sessions).
# `fabric_emulator_notebookutils` is here because contoso-fixtures REQUIRES it,
# not because this repo imports it directly. Omitting it made `uv pip install`
# of the other three unresolvable against the published release:
#
#   x No solution found when resolving dependencies:
#   ╰─▶ Because fabric-emulator-notebookutils was not found ...
#       And because only contoso-fixtures==0.22.0 is available ...
#
# It went unnoticed until 0.22.0 because 0.21.0 shipped NO wheels at all — its
# release job failed its own smoke install — so this list was never resolved
# against a complete release. The emulator side was fixed by shipping the shim
# wheel; this is the consumer half of the same defect, and neither fix works
# without the other.
WHEELS = [
    "contoso_fixtures",
    "contoso_fixtures_advanced",
    "fabric_target",
    "fabric_emulator_notebookutils",
]
EXTRAS = {"fabric_target": "[real,sessions]"}


def pins() -> dict[str, str]:
    """Every pinned image version, by the variable name compose uses."""
    out = {}
    for line in VERSIONS.read_text(encoding="utf-8").splitlines():
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


def install_specs(v=None):
    """What to hand `uv pip install` — the URLs, carrying their extras.

    A bare wheel URL installs the package WITHOUT its extras, and for
    fabric-target that failure is invisible until the first real call: the
    azure-identity import is lazy, so a run would get all the way to
    authenticating before saying anything. The extras belong on the install
    line, which is why this is separate from `wheel_urls` — that one feeds HEAD
    checks, where a PEP 508 spec is not a URL.
    """
    return [
        f"{name.replace('_', '-')}{EXTRAS[name]} @ {url}" if name in EXTRAS else url
        for name, url in zip(WHEELS, wheel_urls(v), strict=True)
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
