"""pcb_info: the toolkit's own reader of a .kicad_pcb.  No KiCad needed; every expected number is worked out by hand in a comment.

Fixtures (tests/pcb_fixtures, see its README):

    clean.kicad_pcb       current format (KiCad 10.0.6), made from src_v7/clean.kicad_pcb by ``kicad-cli pcb upgrade``
        outline  gr_rect (0,0)-(20,20)                          -> 20 x 20 mm
        R1 at (5,15) and R2 at (15,15), pads 1 at (x, 14) net SIG and pads 2 at (x, 16) net GND; R2.2 is through hole
        tracks   SIG  F.Cu (5,14)-(15,14) 0.25 wide             -> 10
                 GND  F.Cu (5,16)-(15,16) 0.25 wide             -> 10
                 GND  B.Cu (10,16)-(15,16) 0.25 wide            -> 5
        via      (10,16) 0.8 / drill 0.4, GND
    clearance.kicad_pcb   clean + N3 (5,5)-(15,5) 0.2 wide -> 10, and N4 (10,5.3)-(10,9) 0.2 wide -> 3.7
    features.kicad_pcb    written by hand in the old format (KiCad 7 syntax); see each test
"""

import json
import math

import pytest

from eng_toolkit.domains.pcb import boardread, sexp
from tests.pcb_testing import call, fixture, no_nonfinite, v


def read(name: str, **kw):
    return boardread.read_board(sexp.decode((__import__("pathlib").Path(fixture(name))).read_bytes()), **kw)


# --------------------------------------------------------------------------- the clean board


@pytest.mark.asyncio
async def test_clean_board_numbers_through_the_gateway():
    r = await call("pcb_info", board_path=fixture("clean.kicad_pcb"))
    assert r.ok, r.error
    assert r.review_status == "unreviewed"
    # tracks: 10 + 10 on F.Cu and 5 on B.Cu = 25 mm in 3 segments
    assert v(r, "track_length_total") == pytest.approx(25.0, abs=1e-9)
    assert r.values["track_length_total"].unit == "millimeter"
    assert v(r, "track_count") == 3
    assert v(r, "via_count") == 1
    assert v(r, "footprint_count") == 2
    assert v(r, "net_count") == 2  # SIG, GND (net 0, no net, is not counted)
    assert v(r, "copper_layers") == 2
    assert v(r, "zone_count") == 0
    # outline: one rectangle from (0,0) to (20,20)
    assert v(r, "board_width") == pytest.approx(20.0) and v(r, "board_height") == pytest.approx(20.0)
    d = r.data
    assert d["file"]["format_version"] == "20260206" and d["file"]["generator_version"] == "10.0"
    assert d["tracks"]["by_layer"] == {"B.Cu": {"count": 1, "length_mm": 5.0}, "F.Cu": {"count": 2, "length_mm": 20.0}}
    assert d["tracks"]["width_mm"]["min"] == d["tracks"]["width_mm"]["max"] == 0.25
    assert d["vias"]["drill_mm"]["min"] == 0.4 and d["vias"]["size_mm"]["max"] == 0.8
    # pads: 3 SMD + 1 through hole (drill 0.6)
    assert d["pads"]["count"] == 4 and d["pads"]["by_type"] == {"smd": 3, "thru_hole": 1}
    assert d["pads"]["drills"]["min"] == 0.6
    # nets: GND = pad 2 of R1 and of R2 (2 pads), tracks F.Cu 10 + B.Cu 5 = 15 mm in 2 segments, 1 via; SIG = 2 pads, 1 track of 10
    nets = {n["name"]: n for n in d["nets"]["list"]}
    assert nets["GND"] == {"name": "GND", "pads": 2, "tracks": 2, "vias": 1, "track_length_mm": 15.0}
    assert nets["SIG"] == {"name": "SIG", "pads": 2, "tracks": 1, "vias": 0, "track_length_mm": 10.0}
    fps = {f["reference"]: f for f in d["footprints"]["list"]}
    assert (fps["R1"]["x_mm"], fps["R1"]["y_mm"]) == (5.0, 15.0) and fps["R2"]["value"] == "10k" and fps["R1"]["side"] == "front"
    assert d["parse_issues"]["count"] == 0
    assert no_nonfinite(r.model_dump(mode="json"))
    json.dumps(r.model_dump(mode="json"), allow_nan=False)  # what an MCP client would insist on


@pytest.mark.asyncio
async def test_clearance_board_adds_two_tracks():
    r = await call("pcb_info", board_path=fixture("clearance.kicad_pcb"))
    # 25 + 10 (N3, (5,5)-(15,5)) + 3.7 (N4, (10,5.3)-(10,9): 9 - 5.3) = 38.7 mm in 5 segments; two more nets: 4
    assert v(r, "track_length_total") == pytest.approx(38.7, abs=1e-9)
    assert v(r, "track_count") == 5 and v(r, "net_count") == 4
    assert r.data["tracks"]["width_mm"] == {"min": 0.2, "max": 0.25, "distinct": [{"mm": 0.2, "count": 2}, {"mm": 0.25, "count": 3}]}


@pytest.mark.asyncio
async def test_board_without_an_outline_has_no_size_and_says_so():
    r = await call("pcb_info", board_path=fixture("no_outline.kicad_pcb"))
    assert r.ok
    assert "board_width" not in r.values and r.data["outline"]["extents_mm"] is None
    assert any("no outline" in w for w in r.warnings)


def test_old_format_and_current_format_give_the_same_numbers():
    # the same board as KiCad 7 wrote its syntax (nets declared as (net 1 "SIG") and referred to by number, fp_text) and as KiCad 10 (nets by name, properties)
    old, new = read("src_v7/clearance.kicad_pcb"), read("clearance.kicad_pcb")
    assert old["file"]["format_version"] == "20221018" and new["file"]["format_version"] == "20260206"
    for key in ("layers", "outline", "nets", "footprints", "pads", "tracks", "vias", "zones"):
        a, b = dict(old[key]), dict(new[key])
        if key == "layers":  # KiCad 10 lists the layers in another order and numbers them differently; the set is the same
            assert sorted(a.pop("all")) == sorted(b.pop("all"))
        assert a == b, key


def test_listing_is_capped_but_counts_are_complete():
    data = read("clean.kicad_pcb", max_list=1)
    assert data["footprints"]["count"] == 2 and data["footprints"]["listed"] == 1
    assert data["nets"]["count"] == 2 and data["nets"]["listed"] == 1


# --------------------------------------------------------------------------- the hand-written board with every kind of element


def test_features_board_by_hand():
    d = read("features.kicad_pcb")
    # layers: F.Cu, In1.Cu, In2.Cu, B.Cu are signal layers = 4 copper layers
    assert d["layers"]["copper_count"] == 4
    out = d["outline"]["extents_mm"]
    # Edge.Cuts: line (0,0)-(40,0); semicircle from (40,0) through (50,10) to (40,20), centre (40,10), r 10, so x reaches 50;
    # lines back to (0,20) and (0,0); the footprint H1 at (0,10) turned 90 deg holds a circle at local (3,0), r 1.
    # KiCad turns a point (x,y) by t: x' = x cos t + y sin t, y' = -x sin t + y cos t -> (0,-3), world (0,7); the circle spans x -1..1.
    # extents: x -1..50 (width 51), y 0..20 (height 20); the Dwgs.User line is not part of the outline
    assert out["min_x"] == pytest.approx(-1.0, abs=1e-9) and out["max_x"] == pytest.approx(50.0, abs=1e-9)
    assert out["min_y"] == pytest.approx(0.0, abs=1e-9) and out["max_y"] == pytest.approx(20.0, abs=1e-9)
    assert out["width"] == pytest.approx(51.0, abs=1e-9) and out["height"] == pytest.approx(20.0, abs=1e-9)
    assert d["outline"]["edge_shapes"] == 5  # 3 lines, 1 arc, 1 circle in the footprint
    # tracks: segment (0,0)-(3,4) = 5 mm (3-4-5) on In1.Cu; arc r 10 through 45 degrees to 90 degrees = 10 * pi/2 = 15.70796 mm on B.Cu
    assert d["tracks"]["segments"] == 1 and d["tracks"]["arcs"] == 1
    assert d["tracks"]["by_layer"]["In1.Cu"]["length_mm"] == pytest.approx(5.0, abs=1e-12)
    assert d["tracks"]["by_layer"]["B.Cu"]["length_mm"] == pytest.approx(5.0 * math.pi, abs=1e-9)
    assert d["tracks"]["total_length_mm"] == pytest.approx(5.0 + 5.0 * math.pi, abs=1e-9)
    # vias: one through (drill 0.4), one blind (0.3), one micro (0.1)
    assert (d["vias"]["through"], d["vias"]["blind"], d["vias"]["micro"]) == (1, 1, 1)
    assert [x["mm"] for x in d["vias"]["drill_mm"]["distinct"]] == [0.1, 0.3, 0.4]
    # footprints: H1 front with no pads, Q1 on the back (layer B.Cu) with 3 pads; pads: smd + np_thru_hole + thru_hole
    assert (d["footprints"]["front"], d["footprints"]["back"]) == (1, 1)
    q1 = next(f for f in d["footprints"]["list"] if f["reference"] == "Q1")
    assert q1["side"] == "back" and q1["pads"] == 3 and q1["angle_deg"] == 180.0 and q1["value"] == "BC547"
    assert d["pads"]["by_type"] == {"np_thru_hole": 1, "smd": 1, "thru_hole": 1}
    assert [x["mm"] for x in d["pads"]["drills"]["distinct"]] == [0.8, 1.5]  # an oval drill is listed by its first (smaller) size
    # zone: rectangle 10 x 5 = 50 mm2 on GND, F.Cu, not filled in the file
    z = d["zones"]["list"][0]
    assert z == {"name": "gndzone", "net": "GND", "layers": ["F.Cu"], "keepout": False, "filled": False, "outline_points": 4, "outline_area_mm2": 50.0}
    assert d["ignored_elements"]["gr_line"] == 1  # the one on Dwgs.User


def test_arc_geometry_by_hand():
    s2 = math.sqrt(0.5)
    quarter = ((10, 0), (10 * s2, 10 * s2), (0, 10))  # centre (0,0), r 10, 0 to 90 degrees counter-clockwise
    assert boardread.arc_length(*quarter) == pytest.approx(10 * math.pi / 2, abs=1e-12)
    assert boardread.arc_bbox(*quarter) == pytest.approx((0.0, 0.0, 10.0, 10.0), abs=1e-12)
    # three quarters the other way: (10,0) through (0,-10) to (0,10): 270 degrees clockwise, length 10 * 3 pi / 2, box is the whole circle
    big = ((10, 0), (0, -10), (0, 10))
    assert boardread.arc_length(*big) == pytest.approx(15 * math.pi, abs=1e-12)
    assert boardread.arc_bbox(*big) == pytest.approx((-10.0, -10.0, 10.0, 10.0), abs=1e-12)
    # an arc whose axis extreme is neither an end nor the middle point: from -45 to +45 degrees with the middle at +20: x reaches r = 10 at 0 degrees
    a = lambda deg: (10 * math.cos(math.radians(deg)), 10 * math.sin(math.radians(deg)))
    box = boardread.arc_bbox(a(-45), a(20), a(45))
    assert box[2] == pytest.approx(10.0, abs=1e-12) and box[0] == pytest.approx(10 * s2, abs=1e-12)
    assert boardread.arc_length((0, 0), (1, 1), (2, 2)) == pytest.approx(2 * math.sqrt(2))  # collinear: a straight line


def test_polygon_area_and_rotation():
    assert boardread.polygon_area([(0, 0), (10, 0), (10, 5), (0, 5)]) == 50.0
    assert boardread.polygon_area([(0, 0), (4, 0), (0, 3)]) == 6.0  # right triangle 4 x 3
    x, y = boardread.rotate(3, 0, 90)
    assert (round(x, 12), round(y, 12)) == (0.0, -3.0)  # a point on +x turns to -y on the screen (y points down): counter-clockwise
    assert boardread.rotate(3, 0, 0) == (3, 0)


def test_damaged_elements_are_skipped_and_listed(tmp_path):
    board = tmp_path / "damaged.kicad_pcb"
    board.write_text(
        '(kicad_pcb (version 20221018) (generator pcbnew)\n'
        '  (net 0 "") (net 1 "A")\n'
        '  (segment (start 0 0) (end 3 4) (width 0.2) (layer "F.Cu") (net 1))\n'
        '  (segment (start 0 0) (end banana 4) (width 0.2) (layer "F.Cu") (net 1))\n'
        '  (segment (start 0 0) (end nan 4) (width 0.2) (layer "F.Cu") (net 1))\n'
        '  (segment (start 0 0) (end 3 4) (layer "F.Cu") (net 1))\n'
        '  (via (at 1 1) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 9))\n'
        ')\n',
        encoding="utf-8",
    )
    d = boardread.read_board(sexp.decode(board.read_bytes()))
    assert d["tracks"]["count"] == 1 and d["tracks"]["total_length_mm"] == 5.0
    # three damaged segments (a word, nan, a missing width) and a via on a net that is not declared
    assert d["parse_issues"]["count"] == 4
    assert any("banana" in t for t in d["parse_issues"]["list"]) and any("not declared" in t for t in d["parse_issues"]["list"])


def test_text_from_the_file_is_cleaned_and_cut():
    assert boardread.clean("a\nb\x00c d") == "a b c d"
    assert len(boardread.clean("x" * 1000)) == boardread.NAME_CHARS


def test_a_board_of_100000_segments_is_read_in_a_few_seconds_and_counted_exactly(tmp_path):
    # segment i goes from (i mod 5000, i div 5000) one step along x: length 1 each -> 100 000 mm in 100 000 segments, on one net
    parts = ['(kicad_pcb (version 20221018) (generator pcbnew) (layers (0 "F.Cu" signal) (31 "B.Cu" signal)) (net 0 "") (net 1 "N")\n']
    parts += [f'(segment (start {i % 5000} {i // 5000}) (end {i % 5000 + 1} {i // 5000}) (width 0.2) (layer "F.Cu") (net 1))\n' for i in range(100_000)]
    parts.append(")")
    d = boardread.read_board("".join(parts))
    assert d["tracks"]["count"] == 100_000 and d["tracks"]["total_length_mm"] == 100_000.0
    assert d["nets"]["list"][0]["tracks"] == 100_000


def test_element_limit_is_input_too_large(monkeypatch):
    monkeypatch.setattr(boardread, "MAX_ELEMENTS", 3)
    with pytest.raises(Exception) as info:
        boardread.read_board("(kicad_pcb (a) (b) (c) (d))")
    assert info.value.family == "input.too_large"
