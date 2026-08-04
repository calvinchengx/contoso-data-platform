"""Invariants for the source systems.

These are static checks on the specs and scripts — no Docker, no network — so
they run on all three platforms in CI from day one.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources"
SPECS = sorted(SOURCES.glob("*/openapi.yaml"))

sys.path.insert(0, str(ROOT / "scripts"))
from materialise_sources import paginate  # noqa: E402


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
        js = (spec.parent / "serve.js").read_text(encoding="utf-8")
        assert "mokapi/file" in js, f"{spec.parent.name}: serves no file"
        assert "faker" not in js, f"{spec.parent.name}: fabricates data"


def test_every_operation_requires_a_key():
    """The extract steps assert that a wrong key is refused. That assertion is
    only meaningful if the API actually demands one."""
    for spec in SPECS:
        text = spec.read_text(encoding="utf-8")
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
    compose = (ROOT / "compose" / "sources.yml").read_text(encoding="utf-8")
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


def test_pages_reassemble_into_exactly_the_original_bytes():
    """Paging that loses or duplicates a row is worse than not paging.

    The whole export must be recoverable from its parts, byte for byte, or
    every count downstream is measuring a different dataset than the vendor
    sent — and would still look self-consistent while doing it.
    """
    body = b"".join(b'{"id":%d}\n' % i for i in range(50_000))
    pages = paginate(body, keep_header=False, page_bytes=64_000)
    assert len(pages) > 1, "the sample did not split, so nothing was tested"
    assert b"".join(pages) == body


def test_every_csv_page_repeats_the_header():
    """Each part has to be independently readable.

    Spark reads the landed directory with `header=True`. A part missing the
    header turns its first record into column names — silently, since the
    result is still a dataframe.
    """
    header = b"customer_id,name,country\n"
    rows = [b"c%d,Name %d,US\n" % (i, i) for i in range(50_000)]
    pages = paginate(header + b"".join(rows), keep_header=True, page_bytes=64_000)
    assert len(pages) > 1, "the sample did not split, so nothing was tested"
    for i, page in enumerate(pages):
        assert page.startswith(header), f"page {i + 1} has no header row"
    rebuilt = header + b"".join(p[len(header) :] for p in pages)
    assert rebuilt == header + b"".join(rows)


def test_paging_never_splits_a_record():
    sample = b"".join(b'{"id":%d}\n' % i for i in range(50_000))
    for page in paginate(sample, False, page_bytes=64_000):
        assert page.endswith(b"\n"), "a page ends mid-record"
        for line in page.splitlines():
            assert line.startswith(b'{"id":') and line.endswith(b"}"), line[:40]


def test_paged_operations_declare_their_paging():
    """The spec is what OpenMetadata ingests and what a client reads.

    An endpoint that pages without saying so leaves every caller to discover it
    by getting a partial answer that looks complete.
    """
    for spec in SPECS:
        text = spec.read_text(encoding="utf-8")
        if "page" not in text:
            continue
        for field in ("X-Total-Pages", "X-Page"):
            assert field in text, f"{spec.parent.name}: pages but no {field} header"
        assert re.search(r"^\s+name: page$", text, re.M), (
            f"{spec.parent.name}: no `page` parameter declared"
        )


def test_serve_scripts_do_not_hardcode_a_page_count():
    """The page count belongs to the data, not to the handler.

    `make sources` decides how many pages there are. A number in the script is
    a second source of truth that goes stale the moment the page size moves,
    and the API would then advertise a count the directory cannot serve.
    """
    for spec in SPECS:
        js = spec.parent / "serve.js"
        if "X-Total-Pages" not in js.read_text(encoding="utf-8"):
            continue
        assert "pages.txt" in js.read_text(encoding="utf-8"), (
            f"{spec.parent.name}: page count is not read from the data"
        )


def test_specs_are_pinned_to_no_host_we_do_not_control():
    for spec in SPECS:
        for url in re.findall(
            r"^\s*- url:\s*(\S+)", spec.read_text(encoding="utf-8"), re.M
        ):
            assert "localhost" in url, f"{spec.parent.name}: points at {url}"


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
