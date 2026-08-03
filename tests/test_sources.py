"""Invariants for the source systems.

These are static checks on the specs and scripts — no Docker, no network — so
they run on all three platforms in CI from day one.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources"
SPECS = sorted(SOURCES.glob("*/openapi.yaml"))


def test_there_is_at_least_one_source():
    assert SPECS, "no source specs found"


def test_every_spec_has_a_serve_script():
    """A spec without a script is a spec mokapi GENERATES bodies for.

    Measured against mokapi v0.50.0: schema generation is random per request and
    random in shape — optional properties are dropped per row — so a generated
    body cannot back an exact-count assertion. Every source must serve bytes
    from the seeded generators instead.
    """
    missing = [s.parent.name for s in SPECS if not (s.parent / "serve.js").exists()]
    assert not missing, f"these sources would serve generated data: {missing}"


def test_serve_scripts_read_files_rather_than_inventing_bodies():
    for spec in SPECS:
        js = (spec.parent / "serve.js").read_text()
        assert "mokapi/file" in js, f"{spec.parent.name}: serves no file"
        assert "faker" not in js, f"{spec.parent.name}: fabricates data"


def test_every_operation_requires_a_key():
    """The extract steps assert that a wrong key is refused. That assertion is
    only meaningful if the API actually demands one."""
    for spec in SPECS:
        text = spec.read_text()
        assert "securitySchemes" in text, f"{spec.parent.name}: no auth declared"
        ops = len(re.findall(r"^\s{6}operationId:", text, re.M))
        secured = len(re.findall(r"^\s{6}security:", text, re.M))
        assert ops == secured, (
            f"{spec.parent.name}: {ops} operations, {secured} declare security"
        )


def test_one_mokapi_instance_per_source():
    """Each vendor is its OWN mokapi, mounted only its own spec and bytes.

    A single instance multiplexing every spec would make one company's outage
    every company's outage, put every vendor under one memory ceiling, and hand
    each of them every other one's export to serve by a path typo. Splitting is
    the only way those three stay false, so the split is checked rather than
    left to whoever edits the compose file next.
    """
    compose = (ROOT / "compose" / "sources.yml").read_text()
    for spec in SPECS:
        vendor = spec.parent.name
        short = vendor.removeprefix("contoso-")
        service = f"mokapi-{short}"
        assert re.search(rf"^  {re.escape(service)}:$", compose, re.M), (
            f"{vendor} has no mokapi instance of its own — expected a "
            f"`{service}:` service in compose/sources.yml"
        )
        # Mounted its own directories, NOT the whole sources tree.
        assert f"../sources/{vendor}:" in compose, f"{vendor}: spec not mounted"
        assert f"../sources/_data/{vendor}:" in compose, f"{vendor}: data not mounted"
    assert "\n  mokapi:" not in compose, (
        "a shared `mokapi` service is back — every vendor gets its own instance"
    )
    # Ports are per vendor, so two instances cannot silently collide.
    published = re.findall(r'^\s+- "\$\{(\w+):-(\d+)\}:', compose, re.M)
    ports = [p for _, p in published]
    assert len(ports) == len(set(ports)), f"two services publish one port: {ports}"


def test_specs_are_pinned_to_no_host_we_do_not_control():
    for spec in SPECS:
        for url in re.findall(r"^\s*- url:\s*(\S+)", spec.read_text(), re.M):
            assert "localhost" in url, f"{spec.parent.name}: points at {url}"
