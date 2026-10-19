"""Adding a domain edits no shared file: domains and their engines are discovered.

The point of these tests is to keep that true.  Several people (or sessions) can each add a domain on their own branch and
the branches then touch disjoint files, apart from the dependency groups in ``pyproject.toml`` and ``uv.lock``.
"""

import importlib
import sys
import textwrap

import pytest

from eng_toolkit import licenses
from eng_toolkit.domains import HISTORICAL_ORDER, DomainInfo, all_domains, package_names


def test_existing_domains_come_first_in_their_historical_order():
    names = [d.name for d in all_domains()]
    assert names[: len(HISTORICAL_ORDER)] == [n for n in HISTORICAL_ORDER if n in names]
    assert set(HISTORICAL_ORDER) <= set(names)


def test_every_domain_package_is_a_registered_domain():
    # a package under eng_toolkit/domains that exposes no DOMAIN would be silently missing from the gateway
    registered = {d.name for d in all_domains()}
    assert set(package_names()) == registered, (set(package_names()) ^ registered)


@pytest.mark.asyncio
async def test_the_gateway_mounts_exactly_what_the_registry_lists():
    from fastmcp import Client

    from eng_toolkit.gateway.server import gateway

    async with Client(gateway) as client:
        mounted = {t.name for t in await client.list_tools()}
    listed = {f"{d.name}_{t}" for d in all_domains() for t in d.tools}
    assert listed <= mounted, listed - mounted
    assert {n for n in mounted if n not in listed} == {"toolkit_list_tools", "toolkit_doctor"}, mounted - listed


FAKE_DOMAIN = textwrap.dedent(
    '''
    from fastmcp import FastMCP

    from eng_toolkit.domains import DomainInfo

    server = FastMCP("zzdemo")

    DOMAIN = DomainInfo(name="zzdemo", title="demo", description="a domain added by a test", engines=("zzdemo-lib",), server=server, tools=())
    '''
)

FAKE_ENGINES = textwrap.dedent(
    """
    from eng_toolkit.licenses import TIER_PERMISSIVE, Engine

    ENGINES = (Engine("zzdemo-lib", "MIT", TIER_PERMISSIVE, "a library only this test knows", ("zzdemo",), optional=True, extra="zzdemo"),)
    """
)


@pytest.fixture
def fake_domain(tmp_path):
    import eng_toolkit.domains as pkg

    root = tmp_path / "zzdemo"
    root.mkdir()
    (root / "__init__.py").write_text(FAKE_DOMAIN, encoding="utf-8")
    (root / "engines.py").write_text(FAKE_ENGINES, encoding="utf-8")
    pkg.__path__.append(str(tmp_path))
    importlib.invalidate_caches()
    yield root
    pkg.__path__.remove(str(tmp_path))
    sys.modules.pop("eng_toolkit.domains.zzdemo", None)
    licenses.KNOWN.pop("zzdemo-lib", None)


def test_a_new_domain_is_found_without_editing_any_registry(fake_domain):
    domains = all_domains()
    assert domains[-1].name == "zzdemo"  # after the historical four, no edit to a list
    assert isinstance(domains[-1], DomainInfo)


def test_a_new_domains_engines_join_the_license_table_from_its_own_file(fake_domain):
    assert "zzdemo-lib" not in licenses.KNOWN
    assert licenses.register_domain_engines() == ["zzdemo-lib"]
    assert licenses.KNOWN["zzdemo-lib"].tier == licenses.TIER_PERMISSIVE
    assert licenses.register_domain_engines() == []  # idempotent: a second pass adds nothing
    assert "uv sync --extra zzdemo" in licenses.install_hint("zzdemo-lib")


def test_two_domains_disagreeing_about_an_engine_is_an_error(fake_domain):
    (fake_domain / "engines.py").write_text(
        FAKE_ENGINES.replace("TIER_PERMISSIVE", "TIER_PERMISSIVE").replace('"zzdemo-lib", "MIT"', '"fluids", "GPL-3.0"'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="differently"):
        licenses.register_domain_engines()


def test_two_domains_with_one_name_is_an_error(fake_domain, tmp_path):
    import eng_toolkit.domains as pkg

    twin = tmp_path / "twin"
    twin.mkdir()
    (twin / "__init__.py").write_text(FAKE_DOMAIN, encoding="utf-8")  # also names itself zzdemo
    try:
        with pytest.raises(ValueError, match="two domains are named"):
            all_domains()
    finally:
        sys.modules.pop("eng_toolkit.domains.twin", None)
