"""Platform-side invariants for the vendor stack.

THE VENDORS THEMSELVES ARE NOT HERE. Their specs, serve scripts and the
invariants about what they send moved to `contoso-sources`, which owns them;
this repository used to carry a byte-identical copy and so tested a duplicate.
What remains is what is genuinely this platform's: that the stack it GENERATES
from that declaration gives every vendor its own instance and its own mounts,
and that this platform's own parsing DDL still matches what the vendor
publishes.
"""

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = pathlib.Path(
    __import__("os").environ.get("SOURCES", ROOT.parent / "contoso-sources")
).resolve()
SPECS = sorted(SOURCES.glob("*/openapi.yaml"))

pytestmark = pytest.mark.skipif(
    not SPECS,
    reason=(
        "contoso-sources is not beside this repository. These tests read the "
        "vendor declaration this platform generates its stack from; set "
        "SOURCES=/path/to/contoso-sources."
    ),
)


def generated_fragment() -> dict:
    """The vendor compose fragment, generated the way `make up` generates it."""
    out = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "sources.py"),
            str(SOURCES / "sources.yaml"),
            str(SOURCES),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)


def test_one_mokapi_instance_per_source():
    """Each vendor is its OWN mokapi, mounted only its own spec and bytes.

    A single instance multiplexing every spec would make one company's outage
    every company's outage, put every vendor under one memory ceiling, and hand
    each of them every other one's export to serve by a path typo. Splitting is
    the only way those three stay false, so the split is checked rather than
    left to whoever edits the compose file next.
    """
    frag = generated_fragment()
    services = frag["services"]
    for spec in SPECS:
        vendor = spec.parent.name
        assert vendor in services, (
            f"{vendor} has no mokapi instance of its own in the generated "
            f"fragment — expected a `{vendor}:` service"
        )
        mounts = services[vendor].get("volumes", [])
        # Mounted its own directories, NOT the whole sources tree. A vendor
        # handed the whole tree can serve another vendor's export by a path typo.
        assert any(f"/sources/{vendor}:" in m for m in mounts), (
            f"{vendor}: spec directory not mounted, or mounted too broadly"
        )
        assert any(f"/sources/_data/{vendor}:" in m for m in mounts), (
            f"{vendor}: data directory not mounted"
        )
        assert not any(m.rstrip(":ro").endswith("/sources") for m in mounts), (
            f"{vendor}: the whole sources tree is mounted"
        )
    assert "mokapi" not in services, (
        "a shared `mokapi` service is back — every vendor gets its own instance"
    )
    # Ports are per vendor, so two instances cannot silently collide.
    published = [p.split(":")[0] for s in services.values() for p in s.get("ports", [])]
    assert len(published) == len(set(published)), (
        f"two services publish the same host port: {published}"
    )


def test_the_web_bronze_schema_matches_the_vendors_published_spec():
    """A renamed field must fail here, not arrive as a column of NULLs.

    bronze cannot infer Contoso Web's shape — the engine's JSON reader is
    NDJSON-only, so array pages are read as text and parsed with `from_json`,
    which needs the schema spelled out. A spelled-out schema is a copy of the
    vendor's, and copies drift. When they drift `from_json` does not raise: the
    field simply parses to NULL, the row count still matches (it comes from the
    array's length), and every assertion in bronze still passes.
    """
    import yaml

    sys.path.insert(0, str(ROOT / "platform"))
    import web_schema

    spec = yaml.safe_load(
        (SOURCES / "contoso-web" / "openapi.yaml").read_text(encoding="utf-8")
    )
    schemas = spec["components"]["schemas"]
    for component, declared in (
        ("WebCustomer", web_schema.WEB_CUSTOMER),
        ("WebProduct", web_schema.WEB_PRODUCT),
        ("WebOrder", web_schema.WEB_ORDER),
        ("WebOrderLine", web_schema.WEB_ORDER_LINE),
    ):
        published = set(schemas[component]["properties"])
        assert set(declared) == published, (
            f"{component}: bronze parses {sorted(declared)} but the vendor "
            f"publishes {sorted(published)}"
        )


def test_the_web_bronze_schema_keeps_every_leaf_a_string():
    """Bronze records what arrived; it does not decide what a price is.

    The POS reader runs with inferSchema off for the same reason. Typing
    `list_price` as a double here would make bronze an interpretation of the
    vendor rather than a copy of it, and would quietly discard whatever the
    vendor actually wrote when the two disagree.
    """
    sys.path.insert(0, str(ROOT / "platform"))
    import web_schema

    def leaves(fields):
        for name, kind in fields.items():
            if isinstance(kind, dict):
                yield from leaves(kind)
            else:
                yield name, kind

    for component in (
        web_schema.WEB_CUSTOMER,
        web_schema.WEB_PRODUCT,
        web_schema.WEB_ORDER,
    ):
        for name, kind in leaves(component):
            assert kind == "string", f"{name} is declared {kind}, not string"
