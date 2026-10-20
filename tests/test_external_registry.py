"""External programs: a domain registers its descriptors in its own engines.py, the catalog is consistent, the doctor shows one row per program."""

import importlib
import json
import re
import sys
import textwrap
from pathlib import Path

import exe_fixtures as F
import pytest
from fastmcp import Client

from eng_toolkit import licenses
from eng_toolkit.contract import ToolResult
from eng_toolkit.external_catalog import CATALOG, CCX, DWG2DXF, GMSH, KICAD_CLI, NGSPICE, YOSYS
from eng_toolkit.gateway.server import gateway

FAKE_DOMAIN = textwrap.dedent(
    '''
    from fastmcp import FastMCP

    from eng_toolkit.domains import DomainInfo

    server = FastMCP("zzext")

    DOMAIN = DomainInfo(name="zzext", title="demo", description="a domain added by a test", engines=(), server=server, tools=())
    '''
)

FAKE_ENGINES = textwrap.dedent(
    """
    from eng_toolkit.licenses import TIER_GPL, ExternalTool

    EXTERNAL_TOOLS = (
        ExternalTool("zz-prog", "GPL-3.0-or-later", TIER_GPL, "a program only this test knows", "ENG_TOOLKIT_ZZ_PROG", ("zz-prog",), "https://example.invalid/zz", ("zzext",)),
    )
    """
)


@pytest.fixture
def fake_domain(tmp_path):
    import eng_toolkit.domains as pkg

    root = tmp_path / "zzext"
    root.mkdir()
    (root / "__init__.py").write_text(FAKE_DOMAIN, encoding="utf-8")
    (root / "engines.py").write_text(FAKE_ENGINES, encoding="utf-8")
    pkg.__path__.append(str(tmp_path))
    importlib.invalidate_caches()
    yield root
    pkg.__path__.remove(str(tmp_path))
    sys.modules.pop("eng_toolkit.domains.zzext", None)
    licenses.EXTERNAL.pop("zz-prog", None)


# --------------------------------------------------------------------------- registration


def test_a_domain_registers_its_program_from_its_own_file(fake_domain):
    assert "zz-prog" not in licenses.EXTERNAL
    assert licenses.register_domain_engines() == []  # the return value lists Python distributions only
    tool = licenses.EXTERNAL["zz-prog"]
    assert tool.tier == licenses.TIER_GPL and tool.env_var == "ENG_TOOLKIT_ZZ_PROG" and tool.optional
    before = dict(licenses.EXTERNAL)
    licenses.register_domain_engines()  # idempotent
    assert licenses.EXTERNAL == before


def test_two_domains_disagreeing_about_a_program_is_an_error(fake_domain):
    licenses.register_domain_engines()
    (fake_domain / "engines.py").write_text(FAKE_ENGINES.replace("GPL-3.0-or-later", "MIT"), encoding="utf-8")
    with pytest.raises(ValueError, match="differently"):
        licenses.register_domain_engines()


def test_the_shipped_domains_register_nothing_yet_and_the_registry_is_a_dict_of_descriptors():
    assert all(isinstance(t, licenses.ExternalTool) and key == t.name for key, t in licenses.EXTERNAL.items())


# --------------------------------------------------------------------------- the catalog


def test_the_catalog_has_the_six_programs_with_consistent_descriptors():
    assert set(CATALOG) == {"dwg2dxf", "ngspice", "yosys", "gmsh", "ccx", "kicad-cli"}
    assert CATALOG["dwg2dxf"] is DWG2DXF and CATALOG["kicad-cli"] is KICAD_CLI
    for name, tool in CATALOG.items():
        assert tool.name == name and tool.optional
        assert tool.env_var == "ENG_TOOLKIT_" + re.sub(r"[^A-Z0-9]", "_", name.upper()), tool.env_var
        assert tool.tier in (licenses.TIER_GPL, licenses.TIER_PERMISSIVE) and tool.tier != licenses.TIER_FORBIDDEN
        assert tool.project.startswith("https://") and tool.executables and len(set(tool.executables)) == len(tool.executables)
        assert re.compile(tool.version_regex).groups <= 1 and tool.version_args
        assert all(" " not in e and "/" not in e and "\\" not in e for e in tool.executables)  # names, not paths
        assert tool.license and tool.role and tool.domains


def test_gpl_programs_are_gpl_isolated_and_the_others_are_permissive():
    gpl = {t.name for t in CATALOG.values() if t.tier == licenses.TIER_GPL}
    assert gpl == {"dwg2dxf", "gmsh", "ccx", "kicad-cli"}
    for tool in CATALOG.values():
        assert licenses.classify_license(tool.license) == tool.tier, (tool.name, tool.license)
    assert (NGSPICE.license, YOSYS.license) == ("BSD-3-Clause", "ISC") and GMSH.license.startswith("GPL") and CCX.license.startswith("GPL")


def test_the_places_to_look_never_come_from_a_document_and_are_absolute_patterns_or_variables():
    for tool in CATALOG.values():
        for location in tool.locations:
            assert location.startswith(("/", "~", "%", "C:\\")), (tool.name, location)
        for addition in tool.path_additions:
            assert addition.startswith("{bin}"), (tool.name, addition)


def test_the_two_programs_that_need_their_own_libraries_say_so():
    assert CCX.path_additions == ("{bin}", "{bin}/../mingw-w64/bin")
    assert YOSYS.path_additions == ("{bin}", "{bin}/../lib")


def test_install_hints_name_the_project_and_the_variable_and_no_commands():
    from eng_toolkit.external import install_hint

    for tool in CATALOG.values():
        hint = install_hint(tool)
        assert tool.project in hint and tool.env_var in hint and "PATH" in hint
        assert not any(bad in hint.lower() for bad in ("curl", "wget", "pip install", "sudo", "irm ", "iex", "apt ", "brew ", "winget", "choco", "conda install"))


def test_nothing_in_the_package_imports_a_gpl_program_or_ships_one():
    root = Path(__file__).resolve().parent.parent
    words = ("libredwg", "dwg2dxf", "gmsh", "calculix", "pygmsh", "kicad", "pcbnew", "pyspice", "ngspice", "yosys")
    for path in (root / "eng_toolkit").rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                assert not any(word in stripped.lower() for word in words if word not in ("dwg2dxf",)) or "external_catalog" in stripped, f"{path.name}:{number} imports {stripped}"
    assert not list((root / "eng_toolkit").rglob("*.dll")) and not list((root / "eng_toolkit").rglob("*.exe"))
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8").lower()
    assert not any(word in pyproject for word in ("libredwg", "gmsh", "calculix", "kicad", "ngspice", "yosys"))


# --------------------------------------------------------------------------- the doctor


async def _doctor():
    async with Client(gateway) as client:
        raw = await client.call_tool("toolkit_doctor", {"scan_env": False}, raise_on_error=False)
    json.dumps(raw.structured_content, allow_nan=False)
    return ToolResult.model_validate(raw.structured_content)


@pytest.mark.asyncio
async def test_the_doctor_shows_one_row_per_registered_program_found_or_not_without_failing(tmp_path, monkeypatch):
    baseline = await _doctor()
    assert baseline.data["external_tools"] == [] or all(r["name"] in CATALOG for r in baseline.data["external_tools"])
    monkeypatch.setitem(licenses.EXTERNAL, "fake-tool", F.TOOL)
    F.no_tool(monkeypatch, tmp_path)
    r = await _doctor()
    (row,) = [t for t in r.data["external_tools"] if t["name"] == "fake-tool"]
    assert row["found"] is False and row["status"] == "not-installed (optional)" and row["declared_tier"] == "gpl-isolated" and row["version"] is None
    assert row["install"] and not any("fake-tool" in f for f in r.data["failures"]) and r.ok == baseline.ok
    fake = F.install_fake(tmp_path, monkeypatch)
    r = await _doctor()
    (row,) = [t for t in r.data["external_tools"] if t["name"] == "fake-tool"]
    assert row["found"] is True and row["path"] == str(fake.shim) and row["version"] == "1.2.3" and row["status"] == "ok" and row["found_via"].startswith("env:")
    assert not any("fake-tool" in f for f in r.data["failures"]) and r.ok == baseline.ok


@pytest.mark.asyncio
async def test_a_required_program_that_is_missing_fails_the_doctor_and_an_optional_one_does_not(tmp_path, monkeypatch):
    from eng_toolkit.licenses import ExternalTool

    required = ExternalTool(**{**F.TOOL.__dict__, "name": "needed-tool", "optional": False})
    monkeypatch.setitem(licenses.EXTERNAL, "needed-tool", required)
    F.no_tool(monkeypatch, tmp_path, required)
    r = await _doctor()
    assert any("needed-tool" in f for f in r.data["failures"]) and r.ok is False
