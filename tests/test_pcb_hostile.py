"""Hostile and broken inputs to the pcb tools, through the MCP client.  None of these needs KiCad: a file that is not a usable board or
schematic is refused by our own reader before any program is started, and the refusal must be a clean result (``ok`` false, a stable
error family, no inf or NaN anywhere, JSON a client accepts)."""

import json
import shutil

import pytest

from tests.pcb_testing import call, fixture, no_nonfinite

GOOD_HEAD = '(kicad_pcb (version 20221018) (generator pcbnew) (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user)) (net 0 "") (net 1 "N")\n'


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return str(path)


async def refused(tool, family, **args):
    r = await call(tool, **args)
    assert r.ok is False, r
    assert r.error.family == family, (r.error.family, r.error.message)
    assert r.value is None and r.values == {} and r.warnings == []
    assert r.review_status == "unreviewed"
    assert no_nonfinite(r.model_dump(mode="json"))
    json.dumps(r.model_dump(mode="json"), allow_nan=False)
    return r


TOOLS_ON_A_BOARD = [("pcb_info", {}), ("pcb_drc", {}), ("pcb_export", {"export": "pos"})]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, extra", TOOLS_ON_A_BOARD)
async def test_text_that_is_not_an_s_expression(tmp_path, tool, extra):
    path = write(tmp_path, "notes.kicad_pcb", "this is not a board, it is a note to myself")
    r = await refused(tool, "input.invalid", board_path=path, **extra)
    assert "does not start with '('" in r.error.message


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, extra", TOOLS_ON_A_BOARD)
async def test_a_png_renamed_to_a_board(tmp_path, tool, extra):
    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x10\x00\x00\x00\x10\x08\x02\x00\x00\x00\x90\x91h6" + bytes(range(256)) * 4
    r = await refused(tool, "input.invalid", board_path=write(tmp_path, "logo.kicad_pcb", png), **extra)
    assert "NUL" in r.error.message or "UTF-8" in r.error.message


@pytest.mark.asyncio
async def test_a_png_with_its_own_name_is_an_unsupported_format(tmp_path):
    await refused("pcb_info", "input.unsupported_format", board_path=write(tmp_path, "logo.png", b"\x89PNG"))


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, extra", TOOLS_ON_A_BOARD)
async def test_nul_bytes_and_bytes_that_are_not_utf8(tmp_path, tool, extra):
    await refused(tool, "input.invalid", board_path=write(tmp_path, "nul.kicad_pcb", GOOD_HEAD + "(a\x00b))"), **extra)
    r = await refused(tool, "input.invalid", board_path=write(tmp_path, "latin.kicad_pcb", GOOD_HEAD.encode() + b'(footprint "caf\xe9"))'), **extra)
    assert "UTF-8" in r.error.message


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, extra", TOOLS_ON_A_BOARD)
async def test_deeply_nested_parentheses(tmp_path, tool, extra):
    # 200 000 levels: a recursive reader would die with a RecursionError (or the server's stack); this one stops at level 65
    path = write(tmp_path, "deep.kicad_pcb", "(kicad_pcb " + "(" * 200_000 + ")" * 200_000 + ")")
    r = await refused(tool, "input.invalid", board_path=path, **extra)
    assert "nested deeper than 64" in r.error.message and r.error.details["reason"] == "depth"


@pytest.mark.asyncio
async def test_unbalanced_parentheses_say_where(tmp_path):
    path = write(tmp_path, "cut.kicad_pcb", GOOD_HEAD + "(segment (start 0 0)\n  (end 1 1\n")
    r = await refused("pcb_info", "input.invalid", board_path=path)
    assert "unclosed" in r.error.message


@pytest.mark.asyncio
async def test_a_file_of_60_mb_is_too_large_before_it_is_read(tmp_path):
    path = tmp_path / "big.kicad_pcb"
    with open(path, "wb") as handle:
        handle.write(GOOD_HEAD.encode())
        handle.write(b" " * (60 * 1024 * 1024))
        handle.write(b")")
    for tool, extra in TOOLS_ON_A_BOARD:
        r = await refused(tool, "input.too_large", board_path=str(path), **extra)
        assert r.error.details["limit_bytes"] == 50_000_000 and r.error.details["bytes"] > 60_000_000


@pytest.mark.asyncio
async def test_one_enormous_string_is_too_large_and_a_big_one_is_cut(tmp_path):
    over = write(tmp_path, "over.kicad_pcb", GOOD_HEAD + '(footprint "x" (property "Reference" "' + "R" * 17_000_000 + '")))')
    r = await refused("pcb_info", "input.too_large", board_path=over)
    assert r.error.details["limit"] == 16_000_000
    big = write(tmp_path, "big.kicad_pcb", GOOD_HEAD + '(footprint "x" (property "Reference" "' + "R" * 5_000_000 + '") (property "Value" "v\nw"))' + ")")
    ok = await call("pcb_info", board_path=big)
    assert ok.ok and ok.data["footprints"]["count"] == 1
    ref = ok.data["footprints"]["list"][0]
    assert len(ref["reference"]) == 200 and ref["value"] == "v w"  # cut to 200 characters, the newline replaced


@pytest.mark.asyncio
@pytest.mark.parametrize("coordinate", ["1e308", "1e999", "-1e308", "10001", "1e30"])
@pytest.mark.parametrize("tool, extra", TOOLS_ON_A_BOARD)
async def test_absurd_coordinates_are_out_of_range_not_infinity(tmp_path, tool, extra, coordinate):
    path = write(tmp_path, "huge.kicad_pcb", GOOD_HEAD + f'(segment (start 0 0) (end {coordinate} 0) (width 0.2) (layer "F.Cu") (net 1)))')
    r = await refused(tool, "input.out_of_range", board_path=path, **extra)
    assert "10000" in r.error.message


@pytest.mark.asyncio
async def test_absurd_sizes_and_angles_are_out_of_range_too(tmp_path):
    for element in (
        '(via (at 0 0) (size 1e308) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1))',
        '(segment (start 0 0) (end 1 1) (width 1e308) (layer "F.Cu") (net 1))',
        '(footprint "x" (layer "F.Cu") (at 0 0 1e300))',
        '(gr_circle (center 0 0) (end 1e308 0) (layer "Edge.Cuts") (width 0.1))',
        '(general (thickness 1e308))',
    ):
        await refused("pcb_info", "input.out_of_range", board_path=write(tmp_path, "x.kicad_pcb", GOOD_HEAD + element + ")"))


@pytest.mark.asyncio
async def test_words_where_numbers_belong_are_skipped_and_never_reach_a_result_as_nan(tmp_path):
    path = write(tmp_path, "words.kicad_pcb", GOOD_HEAD + '(segment (start nan 0) (end inf 0) (width 0.2) (layer "F.Cu") (net 1)) (segment (start 0 0) (end -Infinity 0) (width 0.2) (layer "F.Cu") (net 1)))')
    r = await call("pcb_info", board_path=path)
    assert r.ok and r.data["parse_issues"]["count"] == 2 and r.data["tracks"]["count"] == 0
    assert no_nonfinite(r.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_a_path_to_a_missing_file_and_a_wrong_extension(tmp_path):
    for tool, extra in TOOLS_ON_A_BOARD:
        await refused(tool, "input.file_not_found", board_path=str(tmp_path / "nothing.kicad_pcb"), **extra)
    await refused("pcb_erc", "input.file_not_found", schematic_path=str(tmp_path / "nothing.kicad_sch"))
    await refused("pcb_info", "input.unsupported_format", board_path=fixture("erc_ok.kicad_sch"))
    await refused("pcb_erc", "input.unsupported_format", schematic_path=fixture("clean.kicad_pcb"))


@pytest.mark.asyncio
async def test_a_directory_named_like_a_board(tmp_path):
    (tmp_path / "dir.kicad_pcb").mkdir()
    await refused("pcb_info", "input.file_not_found", board_path=str(tmp_path / "dir.kicad_pcb"))


@pytest.mark.asyncio
async def test_a_schematic_given_as_a_board_and_the_reverse(tmp_path):
    sch = write(tmp_path, "mine.kicad_pcb", open(fixture("erc_ok.kicad_sch"), encoding="utf-8").read())
    r = await refused("pcb_info", "input.invalid", board_path=sch)
    assert "pcb_erc" in r.error.message
    brd = write(tmp_path, "mine.kicad_sch", open(fixture("clean.kicad_pcb"), encoding="utf-8").read())
    r = await refused("pcb_erc", "input.invalid", schematic_path=brd)
    assert "pcb_drc" in r.error.message


@pytest.mark.asyncio
async def test_schematic_with_absurd_coordinates_and_deep_nesting(tmp_path):
    head = '(kicad_sch (version 20231120) (generator "eeschema") '
    await refused("pcb_erc", "input.out_of_range", schematic_path=write(tmp_path, "a.kicad_sch", head + "(wire (pts (xy 0 0) (xy 1e308 0))))"))
    await refused("pcb_erc", "input.invalid", schematic_path=write(tmp_path, "b.kicad_sch", head + "(" * 100_000 + ")" * 100_000 + ")"))
    await refused("pcb_erc", "input.invalid", schematic_path=write(tmp_path, "c.kicad_sch", "not a schematic"))


@pytest.mark.asyncio
async def test_layer_names_with_shell_metacharacters_and_newlines_are_refused(tmp_path):
    board = fixture("clean.kicad_pcb")
    for evil in ["F.Cu;calc", "F.Cu&&dir", "F.Cu\nB.Cu", "$(whoami)", "`id`", "F.Cu|B.Cu", "--help", "-o", "../../x", "F.Cu,B.Cu\n--layers=Edge.Cuts", "F.Cu,,B.Cu", "x" * 5000, "F.Cu%00"]:
        for kind in ("gerbers", "pdf", "svg"):
            r = await refused("pcb_export", "input.invalid", board_path=board, export=kind, layers=evil)
            assert "layer" in r.error.message
    await refused("pcb_export", "input.invalid", board_path=board, export="gerbers", layers="Nope.Cu")  # shaped like a layer name but not on this board
    await refused("pcb_export", "input.invalid", board_path=board, export="step")
    await refused("pcb_export", "input.invalid", board_path=board, export="gerbers; calc")
    await refused("pcb_export", "input.invalid", board_path=board, export="")
    await refused("pcb_export", "input.invalid", board_path=board, export="gerbers", layers=",".join(["F.Cu"] * 50) + ",B.Cu")


@pytest.mark.asyncio
async def test_a_board_that_names_a_hostile_layer_cannot_smuggle_it_into_the_command_line(tmp_path):
    # the layer table of the board itself is data too: a layer called "--output=x" or "F.Cu;calc" passes the "is it on the board" test only if it
    # also passes the name pattern
    head = '(kicad_pcb (version 20221018) (generator pcbnew) (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (50 "--output=x" user) (51 "F.Cu;calc" user)) (net 0 ""))'
    path = write(tmp_path, "evil.kicad_pcb", head)
    for name in ("--output=x", "F.Cu;calc"):
        await refused("pcb_export", "input.invalid", board_path=path, export="pdf", layers=name)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [-1, 1001, 10**9])
async def test_numeric_limits_of_the_tools_are_checked(value):
    await refused("pcb_info", "input.invalid", board_path=fixture("clean.kicad_pcb"), max_listed=value)
    await refused("pcb_drc", "input.invalid", board_path=fixture("clean.kicad_pcb"), max_violations=value)
    await refused("pcb_erc", "input.invalid", schematic_path=fixture("erc_ok.kicad_sch"), max_violations=value)


@pytest.mark.asyncio
async def test_rule_overrides_must_be_lengths_in_range():
    board = fixture("clean.kicad_pcb")
    await refused("pcb_drc", "units.bare_number", board_path=board, min_clearance="0.2")
    await refused("pcb_drc", "units.dimension_mismatch", board_path=board, min_track_width="5 bar")
    await refused("pcb_drc", "input.out_of_range", board_path=board, min_clearance="1e308 mm")
    await refused("pcb_drc", "input.out_of_range", board_path=board, min_track_width="-1 mm")
    await refused("pcb_drc", "input.out_of_range", board_path=board, min_clearance="101 mm")


@pytest.mark.asyncio
async def test_reading_is_limited_to_the_allowed_directories(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(allowed))
    r = await refused("pcb_info", "input.path_not_allowed", board_path=fixture("clean.kicad_pcb"))
    assert "ENG_TOOLKIT_READ_DIRS" in r.error.message
    shutil.copy(fixture("clean.kicad_pcb"), allowed / "ok.kicad_pcb")
    assert (await call("pcb_info", board_path=str(allowed / "ok.kicad_pcb"))).ok
