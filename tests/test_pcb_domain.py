"""The pcb domain as the gateway, the licence check and the doctor see it."""

import ast
import importlib
from pathlib import Path

import pytest
from fastmcp import Client

from eng_toolkit import licenses
from eng_toolkit.domains import all_domains
from eng_toolkit.external_catalog import KICAD_CLI
from eng_toolkit.gateway.server import gateway
from tests.pcb_testing import call

PACKAGE = Path(__file__).resolve().parent.parent / "eng_toolkit" / "domains" / "pcb"


def test_the_domain_is_discovered_with_at_most_six_tools():
    domain = next(d for d in all_domains() if d.name == "pcb")
    assert domain.tools == ("info", "drc", "erc", "export") and len(domain.tools) <= 6
    assert domain.engines == ()


def test_kicad_cli_is_registered_as_an_external_gpl_program_of_this_domain():
    assert licenses.EXTERNAL["kicad-cli"] == KICAD_CLI
    assert KICAD_CLI.tier == licenses.TIER_GPL and KICAD_CLI.domains == ("pcb",)
    engines = importlib.import_module("eng_toolkit.domains.pcb.engines")
    assert engines.EXTERNAL_TOOLS == (KICAD_CLI,) and engines.ENGINES == ()


@pytest.mark.asyncio
async def test_the_gateway_lists_the_four_tools_with_described_basic_parameters():
    async with Client(gateway) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert {"pcb_info", "pcb_drc", "pcb_erc", "pcb_export"} <= set(tools)
    for name in ("pcb_info", "pcb_drc", "pcb_erc", "pcb_export"):
        schema = tools[name].input_schema
        assert len(tools[name].description) > 200
        for pname, prop in schema["properties"].items():
            assert prop["type"] in ("string", "integer", "boolean"), (name, pname)
            assert prop.get("description"), (name, pname)


@pytest.mark.asyncio
async def test_the_doctor_row_for_kicad_cli_names_this_domain():
    result = await call("toolkit_doctor", scan_env=False)
    row = next(r for r in result.data["external_tools"] if r["name"] == "kicad-cli")
    assert row["domains"] == ["pcb"] and row["declared_tier"] == "gpl-isolated" and row["optional"] is True
    assert row["status"] in ("ok", "not-installed (optional)")


def test_nothing_in_the_package_imports_kicad_or_runs_a_shell():
    forbidden_modules = {"pcbnew", "kicad", "eeschema", "subprocess", "os"}  # the layer is the only place a program is started
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            assert not set(names) & forbidden_modules, (path.name, names)
        text = path.read_text(encoding="utf-8")
        for danger in ("shell=True", "os.system", "eval(", "exec(", "pickle", "__import__"):
            assert danger not in text, (path.name, danger)


def test_no_binary_is_shipped_with_the_domain():
    assert not [p for p in PACKAGE.rglob("*") if p.suffix.lower() in (".exe", ".dll", ".so", ".dylib")]
