"""pcb_drc, pcb_erc, pcb_export and pcb_info(check_with_kicad) against the real kicad-cli (skipped when it is not installed).

The boards are small and hand-written (tests/pcb_fixtures/README.md says how); every expected number is worked out in a comment.
Geometry of ``clean.kicad_pcb``: outline (0,0)-(20,20); R1 at (5,15) and R2 at (15,15), their pads 1 at (x,14) on SIG and pads 2 at
(x,16) on GND (pad size 1 x 1); SIG track (5,14)-(15,14), GND track (5,16)-(15,16) on F.Cu and (10,16)-(15,16) on B.Cu, all 0.25 wide;
a via at (10,16) (0.8 / 0.4).  KiCad 10.0.6 with its built-in defaults: clearance 0.2 mm, minimum track width 0.2 mm.
"""

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from tests.pcb_testing import call, fixture, needs_kicad, no_nonfinite, v, violation_count

pytestmark = [needs_kicad, pytest.mark.asyncio]


def items(result, kind):
    """Positions ``(x, y)`` of the items of every violation of type ``kind`` in a DRC result, in the order KiCad lists them."""
    rows = [x for x in result.data["violations"]["list"] if x["type"] == kind]
    return [[(i["x_mm"], i["y_mm"]) for i in row["items"]] for row in rows]


async def test_a_clean_board_has_no_violation_at_all():
    r = await call("pcb_drc", board_path=fixture("clean.kicad_pcb"))
    assert r.ok, r.error
    assert (v(r, "error_count"), v(r, "warning_count"), v(r, "unconnected_count"), v(r, "violation_count")) == (0, 0, 0, 0)
    assert r.data["violations"]["by_type"] == [] and r.data["unconnected"]["count"] == 0
    assert r.data["rule_set"]["source"] == "kicad_defaults" and r.data["kicad"]["version"].startswith("10.")
    assert r.data["board"] == {"format_version": "20260206", "copper_layers": 2, "tracks": 3, "footprints": 2}
    assert no_nonfinite(r.model_dump(mode="json"))


async def test_two_tracks_0_1_mm_apart_give_exactly_one_clearance_violation():
    r = await call("pcb_drc", board_path=fixture("clearance.kicad_pcb"))
    assert r.ok, r.error
    # N3 runs (5,5)-(15,5) and N4 (10,5.3)-(10,9), both 0.2 wide.  Copper of N3 reaches y = 5 + 0.1 = 5.1, copper of N4 starts at
    # y = 5.3 - 0.1 = 5.2 (round end cap): the gap is 5.2 - 5.1 = 0.1 mm, below the 0.2 mm of the default net class
    assert violation_count(r, "clearance") == 1
    assert v(r, "error_count") == 1
    (row,) = [x for x in r.data["violations"]["list"] if x["type"] == "clearance"]
    assert row["severity"] == "error"
    assert row["required_mm"] == pytest.approx(0.2) and row["actual_mm"] == pytest.approx(0.1)
    # positions are those of the two tracks KiCad names: where each one starts
    assert sorted((i["x_mm"], i["y_mm"]) for i in row["items"]) == [(5.0, 5.0), (10.0, 5.3)]
    # the two free ends the board has are warnings, not errors: each track has one unconnected end ((5,5) start of N3 .. 4 ends in all, KiCad reports one per track)
    assert violation_count(r, "track_dangling") == 2 and v(r, "warning_count") == 2


async def test_two_pads_of_one_net_without_a_track_give_exactly_one_unconnected_item():
    r = await call("pcb_drc", board_path=fixture("unconnected.kicad_pcb"))
    assert r.ok, r.error
    # the SIG track between pad 1 of R1 (5,14) and pad 1 of R2 (15,14) is missing: one missing connection between those two pads
    assert v(r, "unconnected_count") == 1 and v(r, "error_count") == 0 and v(r, "violation_count") == 0
    (u,) = r.data["unconnected"]["list"]
    assert u["type"] == "unconnected_items"
    assert [(i["x_mm"], i["y_mm"]) for i in u["items"]] == [(5.0, 14.0), (15.0, 14.0)]
    assert [("[SIG]" in i["description"]) for i in u["items"]] == [True, True]


async def test_a_track_narrower_than_the_minimum_gives_exactly_one_track_width_violation():
    r = await call("pcb_drc", board_path=fixture("narrow.kicad_pcb"))
    assert r.ok, r.error
    # the GND track on F.Cu is 0.1 wide, the minimum is 0.2: one violation, at the track's start (5,16); the other two tracks are 0.25
    assert violation_count(r, "track_width") == 1 and v(r, "error_count") == 1
    (row,) = [x for x in r.data["violations"]["list"] if x["type"] == "track_width"]
    assert row["required_mm"] == pytest.approx(0.2) and row["actual_mm"] == pytest.approx(0.1)
    assert [(i["x_mm"], i["y_mm"]) for i in row["items"]] == [(5.0, 16.0)]


async def test_a_board_without_an_edge_cuts_outline_always_gets_one_invalid_outline_error():
    r = await call("pcb_drc", board_path=fixture("no_outline.kicad_pcb"))
    assert r.ok, r.error
    assert violation_count(r, "invalid_outline") == 1 and v(r, "error_count") == 1
    assert any("no outline" in w for w in r.warnings)  # documented, so that nobody reads it as a fault of the rest of the board


async def test_min_track_width_override_is_a_custom_rule_we_write():
    # clean has 3 tracks of 0.25; a minimum of 0.3 makes all 3 violate it: 3 track_width errors, each 0.25 against 0.3
    r = await call("pcb_drc", board_path=fixture("clean.kicad_pcb"), min_track_width="0.3 mm")
    assert violation_count(r, "track_width") == 3 and v(r, "error_count") == 3
    assert {(x["required_mm"], x["actual_mm"]) for x in r.data["violations"]["list"]} == {(0.3, 0.25)}
    assert r.data["rule_set"]["overrides_mm"] == {"track_width": 0.3} and r.data["rule_set"]["source"] == "overrides"
    # 0.25 mm is not below 0.25 mm
    assert v(await call("pcb_drc", board_path=fixture("clean.kicad_pcb"), min_track_width="0.25 mm"), "error_count") == 0
    # units are converted: 0.012 inch = 0.3048 mm
    r = await call("pcb_drc", board_path=fixture("clean.kicad_pcb"), min_track_width="0.012 inch")
    assert violation_count(r, "track_width") == 3


async def test_min_clearance_override_moves_the_clearance_violation_in_and_out():
    board = fixture("clearance.kicad_pcb")
    assert violation_count(await call("pcb_drc", board_path=board, min_clearance="0.05 mm"), "clearance") == 0  # gap 0.1 > 0.05
    r = await call("pcb_drc", board_path=board, min_clearance="0.15 mm")  # gap 0.1 < 0.15
    assert violation_count(r, "clearance") == 1
    row = next(x for x in r.data["violations"]["list"] if x["type"] == "clearance")
    assert (row["required_mm"], row["actual_mm"]) == (0.15, 0.1)


async def test_the_raw_report_is_an_artifact_and_the_listing_is_capped_but_counts_are_not():
    r = await call("pcb_drc", board_path=fixture("clearance.kicad_pcb"), max_violations=1)
    artifact = next(a for a in r.artifacts if a.path.endswith("drc_report.json"))
    raw = Path(artifact.path).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == artifact.sha256
    assert json.loads(raw)["kicad_version"].startswith("10.")
    assert r.data["violations"]["total"] == 3 and r.data["violations"]["listed"] == 1 and len(r.data["violations"]["list"]) == 1
    assert sum(x["count"] for x in r.data["violations"]["by_type"]) == 3
    assert any("only 1 of 3" in w for w in r.warnings)
    assert r.data["violations"]["list"][0]["severity"] == "error"  # errors first


async def test_the_board_next_to_its_project_file_uses_the_numbers_we_allow(tmp_path):
    board = tmp_path / "mine.kicad_pcb"
    board.write_bytes(Path(fixture("clean.kicad_pcb")).read_bytes())
    project = {
        "meta": {"filename": "mine.kicad_pro", "version": 3},
        "board": {"design_settings": {"rules": {"min_track_width": 0.3, "min_clearance": 0.0, "nonsense": 1}}},
        "pcbnew": {"page_layout_descr_file": "C:/Windows/win.ini"},
        "text_variables": {"SECRET": "hunter2"},
        "libraries": {"pinned_footprint_libs": ["../../outside"]},
    }
    (tmp_path / "mine.kicad_pro").write_text(json.dumps(project), encoding="utf-8")
    r = await call("pcb_drc", board_path=str(board))
    assert r.ok, r.error
    # the project's minimum track width 0.3 is used: 3 tracks of 0.25 violate it
    assert violation_count(r, "track_width") == 3
    rule_set = r.data["rule_set"]
    assert rule_set["source"] == "project_file" and rule_set["project_file"]["rules"] == {"min_clearance": 0.0, "min_track_width": 0.3}
    assert rule_set["project_file"]["left_out"] == ["libraries", "pcbnew", "text_variables"]
    # the project file KiCad was given is the one we wrote: none of the left-out parts is in it
    written = (Path(r.data["job_dir"]) / "work" / "board.kicad_pro").read_text(encoding="utf-8")
    for leftover in ("win.ini", "hunter2", "outside", "nonsense", "pcbnew", "text_variables"):
        assert leftover not in written
    # the same board with the project file ignored: KiCad's defaults, no violation
    off = await call("pcb_drc", board_path=str(board), use_project_files=False)
    assert violation_count(off, "track_width") == 0 and off.data["rule_set"]["source"] == "kicad_defaults"
    # both files were read through the checked copy path, so provenance names them
    assert [Path(f.path).name for f in r.provenance.input_files] == ["mine.kicad_pcb", "mine.kicad_pro"]


async def test_a_custom_rules_file_is_used_when_it_holds_only_rules(tmp_path):
    board = tmp_path / "mine.kicad_pcb"
    board.write_bytes(Path(fixture("clean.kicad_pcb")).read_bytes())
    rules = tmp_path / "mine.kicad_dru"
    rules.write_text('(version 1)\n# a comment\n(rule "wide tracks"\n\t(constraint track_width (min 0.3mm)))\n', encoding="utf-8")
    r = await call("pcb_drc", board_path=str(board))
    assert violation_count(r, "track_width") == 3 and r.data["rule_set"]["custom_rules_file"] == {"used": True, "rules": 1, "bytes": rules.stat().st_size}
    # anything but (version) and (rule) at the top level and the file is not used, with a warning
    rules.write_text('(version 1)\n(import "other.kicad_dru")\n', encoding="utf-8")
    r = await call("pcb_drc", board_path=str(board))
    assert r.ok and violation_count(r, "track_width") == 0
    assert r.data["rule_set"]["custom_rules_file"]["used"] is False
    assert any("custom rules file" in w and "import" in w for w in r.warnings)
    # a rules file over the size cap is skipped without reading it into KiCad
    rules.write_text("(version 1)\n" + "#" * 300_000 + "\n", encoding="utf-8")
    r = await call("pcb_drc", board_path=str(board))
    assert r.ok and r.data["rule_set"]["custom_rules_file"] == {"used": False}


async def test_a_board_with_a_hostile_file_name_is_copied_to_a_fixed_name(tmp_path):
    odd = tmp_path / "a b; calc & dir $(x) `y` 板.kicad_pcb"
    odd.write_bytes(Path(fixture("clean.kicad_pcb")).read_bytes())
    r = await call("pcb_drc", board_path=str(odd))
    assert r.ok, r.error
    assert r.data["violations"]["total"] == 0
    assert (Path(r.data["job_dir"]) / "work" / "board.kicad_pcb").is_file()


async def test_erc_clean_schematic_has_no_error():
    r = await call("pcb_erc", schematic_path=fixture("erc_ok.kicad_sch"))
    assert r.ok, r.error
    # R1 and R2 are wired pin 1 to pin 1 and pin 2 to pin 2: every pin is connected, nothing to report but the library note below
    assert v(r, "error_count") == 0
    assert violation_count(r, "pin_not_connected") == 0
    # the symbols come from the library "Device", which is not visible to the run (tables are not read): 2 warnings, one per symbol
    assert violation_count(r, "lib_symbol_issues") == 2 and v(r, "warning_count") == 2
    assert any("library tables are deliberately not read" in w for w in r.warnings)


async def test_erc_counts_the_unconnected_pins():
    r = await call("pcb_erc", schematic_path=fixture("erc_open.kicad_sch"))
    assert r.ok, r.error
    # the wire between pin 2 of R1 (100.33, 83.82) and pin 2 of R2 (115.57, 83.82) is missing: two pins have nothing on them
    assert violation_count(r, "pin_not_connected") == 2 and v(r, "error_count") == 2
    pins = sorted((i["x_mm"], i["y_mm"]) for row in r.data["violations"]["list"] if row["type"] == "pin_not_connected" for i in row["items"])
    # pin 2 of a resistor at (x, 80.01) with its pin end 3.81 below: y = 80.01 + 3.81 = 83.82 (KiCad prints these 100 times smaller; calibrated)
    assert pins == [(100.33, 83.82), (115.57, 83.82)]
    assert "factor 100 applied" in r.data["position_calibration"]
    assert all(row["sheet"] == "/" for row in r.data["violations"]["list"])
    artifact = next(a for a in r.artifacts if a.path.endswith("erc_report.json"))
    assert hashlib.sha256(Path(artifact.path).read_bytes()).hexdigest() == artifact.sha256


async def test_gerbers_come_as_files_with_their_hashes_and_one_zip():
    r = await call("pcb_export", board_path=fixture("clean.kicad_pcb"), export="gerbers", layers="F.Cu,B.Cu,Edge.Cuts")
    assert r.ok, r.error
    names = sorted(f["name"] for f in r.data["files"])
    # round 3: the files carry the board's own name (clean.kicad_pcb -> clean-*), the Excellon drill file is in the set, the zip has the name too
    assert names == sorted(["clean-B_Cu.gbl", "clean-Edge_Cuts.gm1", "clean-F_Cu.gtl", "clean-job.gbrjob", "clean.drl", "clean-gerbers.zip"])
    by_path = {Path(a.path).name: a for a in r.artifacts}
    for f in r.data["files"]:
        raw = Path(by_path[f["name"]].path).read_bytes()
        assert f["bytes"] == len(raw) and f["sha256"] == hashlib.sha256(raw).hexdigest() == by_path[f["name"]].sha256
    with zipfile.ZipFile(io.BytesIO(Path(by_path["clean-gerbers.zip"].path).read_bytes())) as archive:
        assert sorted(archive.namelist()) == sorted(n for n in names if n != "clean-gerbers.zip")  # layers, job file AND the drill file
        assert archive.testzip() is None
    # the copper layer is a Gerber file: X2 header and the end-of-file code
    top = Path(by_path["clean-F_Cu.gtl"].path).read_text(encoding="utf-8")
    assert top.startswith("%TF.GenerationSoftware,KiCad") and top.rstrip().endswith("M02*")
    assert v(r, "file_count") == 5
    assert r.data["board_name"] == "clean" and r.data["package"]["zip"] == "clean-gerbers.zip"
    # the board's own name is also the project id inside the files, so a fabricator sees it
    assert "%TF.ProjectId,clean," in top


async def test_the_default_gerber_set_is_every_copper_mask_silk_paste_and_edge_layer():
    r = await call("pcb_export", board_path=fixture("clean.kicad_pcb"), export="gerbers")
    # 2 copper + mask, silk, paste on both sides = 8, + Edge.Cuts = 9, + the job file + the drill file = 11 files (the zip is the 12th item of data.files)
    assert v(r, "file_count") == 11 and len(r.data["files"]) == 12
    assert sorted(r.data["layers"]) == sorted(["F.Cu", "B.Cu", "B.Paste", "F.Paste", "B.SilkS", "F.SilkS", "B.Mask", "F.Mask", "Edge.Cuts"])


async def test_drill_file_lists_the_via_and_the_through_hole_pad():
    r = await call("pcb_export", board_path=fixture("clean.kicad_pcb"), export="drill")
    assert r.ok, r.error
    (f,) = r.data["files"]
    raw = Path(next(a.path for a in r.artifacts if a.path.endswith("clean.drl"))).read_bytes()
    text = raw.decode("utf-8")
    # via drill 0.4 at (10,16) and pad drill 0.6 at (15,16); an Excellon file has y pointing up, so -16
    assert "T1C0.400" in text and "T2C0.600" in text
    assert "X10.0Y-16.0" in text and "X15.0Y-16.0" in text
    assert f["format"] == "excellon" and f["sha256"] == hashlib.sha256(raw).hexdigest()


async def test_position_file_agrees_with_our_own_reader():
    r = await call("pcb_export", board_path=fixture("clean.kicad_pcb"), export="pos")
    text = Path(next(a.path for a in r.artifacts if a.path.endswith("positions.csv"))).read_text(encoding="utf-8")
    rows = [line.split(",") for line in text.strip().splitlines()[1:]]
    # R1 at (5,15), R2 at (15,15); a position file has y up, so -15
    assert [(row[0], float(row[3]), float(row[4])) for row in rows] == [('"R1"', 5.0, -15.0), ('"R2"', 15.0, -15.0)]
    info = await call("pcb_info", board_path=fixture("clean.kicad_pcb"))
    ours = {f["reference"]: (f["x_mm"], f["y_mm"]) for f in info.data["footprints"]["list"]}
    assert ours == {"R1": (5.0, 15.0), "R2": (15.0, 15.0)}


async def test_pdf_and_svg_are_single_files_of_the_chosen_layers():
    pdf = await call("pcb_export", board_path=fixture("clean.kicad_pcb"), export="pdf", layers="F.Cu,Edge.Cuts")
    assert pdf.ok, pdf.error
    assert Path(next(a.path for a in pdf.artifacts if a.path.endswith("clean.pdf"))).read_bytes().startswith(b"%PDF-")
    svg = await call("pcb_export", board_path=fixture("clean.kicad_pcb"), export="svg", layers="Edge.Cuts")
    text = Path(next(a.path for a in svg.artifacts if a.path.endswith("clean.svg"))).read_text(encoding="utf-8")
    assert "<svg" in text
    assert pdf.data["layers"] == ["F.Cu", "Edge.Cuts"] and svg.data["layers"] == ["Edge.Cuts"]


@pytest.mark.parametrize("name", ["clean.kicad_pcb", "clearance.kicad_pcb"])
async def test_our_reader_and_kicad_cli_agree_where_both_give_a_number(name):
    r = await call("pcb_info", board_path=fixture(name), check_with_kicad=True)
    assert r.ok, r.error
    cross = r.data["kicad_crosscheck"]
    assert cross["checked"] and cross["all_agree"], cross
    kinds = {c["what"] for c in cross["comparisons"]}
    assert {"board_width_mm", "pads_smd", "pads_through_hole", "vias_through", "track_width_min_mm", "footprint_positions"} <= kinds


async def test_cross_check_names_the_one_place_the_two_readers_differ_on_the_hand_written_board():
    # features.kicad_pcb has a circle on Edge.Cuts inside footprint H1 that crosses the board's left edge: KiCad treats it as a cut-out and its
    # board width is that of the board area (50, 49.9975 after it approximates the arc), ours is the extent of every Edge.Cuts shape (51)
    r = await call("pcb_info", board_path=fixture("features.kicad_pcb"), check_with_kicad=True)
    cross = r.data["kicad_crosscheck"]
    assert [c["what"] for c in cross["comparisons"] if not c["agree"]] == ["board_width_mm"]
    assert next(c for c in cross["comparisons"] if c["what"] == "board_height_mm")["agree"]
    assert any("disagree on: board_width_mm" in w for w in r.warnings)


async def test_a_board_kicad_cannot_load_is_an_engine_failure_with_its_own_words(tmp_path):
    # well-formed text that our reader takes, but a board with a single copper layer is not one KiCad 10.0.6 will load ("1 is not a valid layer count")
    path = tmp_path / "unloadable.kicad_pcb"
    path.write_text(
        '(kicad_pcb (version 20221018) (generator pcbnew) (layers (0 "F.Cu" signal)) (net 0 "") '
        '(zone (net 0) (net_name "") (layer "F.Cu") (hatch edge 0.5) (polygon (pts (xy 0 0) (xy 10 0) (xy 10 10)))))',
        encoding="utf-8",
    )
    r = await call("pcb_drc", board_path=str(path))
    assert r.ok is False and r.error.family == "engine.failure"
    assert "exited with code 3" in r.error.message and "Failed to load board" in r.error.message
    assert "<job>" in r.error.message and str(tmp_path) not in r.error.message  # paths are hidden


async def test_text_that_is_a_board_to_us_but_empty_is_still_checked_by_kicad(tmp_path):
    # a version number from the future and a board with nothing in it are both loaded by KiCad 10.0.6; the only finding is the missing outline
    for text in ("(kicad_pcb (version 99999999) (generator pcbnew))", "(kicad_pcb (version 20221018) (generator pcbnew))"):
        path = tmp_path / "empty.kicad_pcb"
        path.write_text(text, encoding="utf-8")
        r = await call("pcb_drc", board_path=str(path))
        assert r.ok and violation_count(r, "invalid_outline") == 1 and v(r, "violation_count") == 1


def _many_clearance_pairs(tmp_path, pairs, name):
    """clearance.kicad_pcb plus ``pairs`` pairs of 0.5 mm tracks of nets N3 and N4, 0.3 mm centre to centre and 0.2 wide: a 0.1 mm gap, one clearance violation each."""
    text = Path(fixture("clearance.kicad_pcb")).read_text(encoding="utf-8")
    segments = []
    for k in range(pairs):
        x, y = 30 + (k % 30), 30 + (k // 30)
        for net, dy in (("N3", 0.0), ("N4", 0.3)):
            segments.append(f'\t(segment\n\t\t(start {x} {y + dy})\n\t\t(end {x + 0.5} {y + dy})\n\t\t(width 0.2)\n\t\t(layer "F.Cu")\n\t\t(net "{net}")\n'
                            f'\t\t(uuid "00000000-0000-4000-8000-{k:06d}{0 if net == "N3" else 1:06d}")\n\t)\n')
    board = tmp_path / name
    board.write_text(text.rstrip().removesuffix(")") + "".join(segments) + ")\n", encoding="utf-8")
    return board


async def test_a_clearance_count_at_kicads_listing_limit_is_a_lower_bound(tmp_path):
    # UAT E1-1: 600 real violations, KiCad lists 499 at every threshold; nothing said the number was capped
    board = _many_clearance_pairs(tmp_path, 600, "many.kicad_pcb")
    r = await call("pcb_drc", board_path=str(board), max_violations=0)
    assert r.ok, r.error
    assert violation_count(r, "clearance") == 499
    # the 1200 free track ends give 600+ track_dangling warnings, which KiCad stops at 199 (the other limit)
    assert {(x["type"], x["count"]) for x in r.data["violations"]["at_kicad_limit"]} == {("clearance", 499), ("track_dangling", 199)}
    assert any("clearance (error) is reported 499 times" in w and "at least" in w and "lower bound" in w for w in r.warnings)
    few = _many_clearance_pairs(tmp_path, 5, "few.kicad_pcb")
    r = await call("pcb_drc", board_path=str(few), max_violations=0)
    assert violation_count(r, "clearance") == 6 and r.data["violations"]["at_kicad_limit"] == []  # 1 in the fixture and 5 added
    assert not any("lower bound" in w for w in r.warnings)


async def test_erc_library_warnings_are_a_separate_group():
    r = await call("pcb_erc", schematic_path=fixture("erc_ok.kicad_sch"))
    assert r.ok, r.error
    library = r.data["violations"]["library_warnings"]
    assert library["count"] == 2 and [row["type"] for row in library["by_type"]] == ["lib_symbol_issues"]
    assert v(r, "library_warning_count") == 2 and v(r, "warning_count") == 2  # still counted in KiCad's own totals
    assert r.data["violations"]["design_warnings"] == 0
    assert all(row["group"] == "library" for row in r.data["violations"]["list"])
    assert any("2 of the 2 warnings are library warnings" in w for w in r.warnings)


async def test_a_relative_workspace_path_works_for_kicad(tmp_path, monkeypatch):
    # UAT item 1: "Failed to load board" when ENG_TOOLKIT_WS was relative
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENG_TOOLKIT_WS", "relative-ws")
    r = await call("pcb_drc", board_path=fixture("clean.kicad_pcb"))
    assert r.ok, r.error
    assert r.data["violations"]["total"] == 0
