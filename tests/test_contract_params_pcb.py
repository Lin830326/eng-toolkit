"""pcb refusals about a tool argument carry details.param as a JSON pointer to it."""

import pytest

from tests.pcb_testing import call, fixture


async def refused(tool: str, family: str, **arguments):
    r = await call(tool, **arguments)
    assert r.ok is False
    assert r.error.family == family, r.error
    return r.error.details


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [-5, 1001])
async def test_pcb_info_max_listed_out_of_range(value):
    details = await refused("pcb_info", "input.invalid", board_path=fixture("clean.kicad_pcb"), max_listed=value)
    assert details["param"] == "/max_listed"


@pytest.mark.asyncio
async def test_pcb_drc_max_violations_out_of_range():
    details = await refused("pcb_drc", "input.invalid", board_path=fixture("clean.kicad_pcb"), max_violations=-5)
    assert details["param"] == "/max_violations"


@pytest.mark.asyncio
async def test_pcb_erc_max_violations_out_of_range():
    details = await refused("pcb_erc", "input.invalid", schematic_path=fixture("erc_ok.kicad_sch"), max_violations=-5)
    assert details["param"] == "/max_violations"


@pytest.mark.asyncio
async def test_pcb_export_unknown_export():
    details = await refused("pcb_export", "input.invalid", board_path=fixture("clean.kicad_pcb"), export="bogus")
    assert details["param"] == "/export"


@pytest.mark.asyncio
async def test_pcb_export_unknown_layer():
    details = await refused("pcb_export", "input.invalid", board_path=fixture("clean.kicad_pcb"), export="svg", layers="Nope.Cu")
    assert details["param"] == "/layers"


@pytest.mark.asyncio
async def test_pcb_info_a_file_that_is_not_a_board_points_at_board_path(tmp_path):
    path = tmp_path / "notes.kicad_pcb"
    path.write_text("this is not a board, it is a note to myself", encoding="utf-8")
    details = await refused("pcb_info", "input.invalid", board_path=str(path))
    assert details["param"] == "/board_path"


@pytest.mark.asyncio
async def test_pcb_info_a_schematic_saved_as_a_board_points_at_board_path(tmp_path):
    # this refusal is raised inside the board reader, which does not know the argument name
    path = tmp_path / "sheet.kicad_pcb"
    path.write_text("(kicad_sch (version 20250114))", encoding="utf-8")
    details = await refused("pcb_info", "input.invalid", board_path=str(path))
    assert details["param"] == "/board_path"
