"""Sheet, scale and view placement, checked against hand calculations.  Pure: no OpenCASCADE and no DXF library.

The probe part is the 100 x 50 x 20 mm block.  Front, top and right views in third angle, 1:1, A4 (297 x 210):

    front   u = X 0..100, v = Z 0..20      top    u = X 0..100, v = Y 0..50      right   u = Y 0..50, v = Z 0..20
    dimensions: X and Z on the front view (a row below it, a row to its left), Y on the top view (a row to its left)
    column 0 (front, top): 8 mm of dimension row + 100 mm; column 1 (right): 50 mm; gap 15  -> 8 + 100 + 15 + 50 = 173
    row 0 (front, right):  8 mm of dimension row + 20 mm;  row 1 (top): 50 mm; gap 15       -> 8 + 20 + 15 + 50  =  93
    A4 frame inside 20 mm left and 10 mm elsewhere: x 20..287, y 10..200 = 267 x 190 = 50730 mm^2
    title block 180 x 40 at x 107..287, y 10..50; views go above it with 5 mm clear: x 25..282, y 55..195 = 257 x 140
    173 x 93 fits, centred: lower left corner ((25 + 282 - 173) / 2, (55 + 195 - 93) / 2) = (67, 78.5)
    front: left = 67 + 8 = 75, bottom = 78.5 + 8 = 86.5; top: bottom = 78.5 + (8 + 20 + 15) = 121.5; right: left = 67 + 108 + 15 = 190
    the footprint 173 * 93 = 16089 is more than a quarter of 50730 (12682.5), so the part is not enlarged
"""

import pytest
from drawing_fixtures import projection

from eng_toolkit.domains.drawing import conventions as C
from eng_toolkit.domains.drawing import sheet as S
from eng_toolkit.errors import ToolFailure

EXPECTED_SHEETS = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0), "A1": (841.0, 594.0), "A0": (1189.0, 841.0)}


def views(names, box=(100.0, 50.0, 20.0)):
    return [projection(n, box) for n in S.parse_views(",".join(names))]


def compose(names=("front", "top", "right"), box=(100.0, 50.0, 20.0), **kwargs) -> S.Drawing:
    kwargs.setdefault("method", "third_angle")
    return S.compose(views(names, box), **kwargs)


def by_name(drawing: S.Drawing) -> dict[str, S.PlacedView]:
    return {v.name: v for v in drawing.views}


def failure(call) -> ToolFailure:
    with pytest.raises(ToolFailure) as caught:
        call()
    return caught.value


# --------------------------------------------------------------------------- constants


def test_sheets_are_the_iso_5457_landscape_sizes():
    assert S.SHEETS == EXPECTED_SHEETS


def test_scales_are_the_iso_5455_ratios():
    assert [S.scale_text(s) for s in S.SCALES] == ["1:1", "1:2", "1:5", "1:10", "1:20", "1:50", "1:100", "2:1", "5:1", "10:1"]


# --------------------------------------------------------------------------- the probe part on A4


def test_the_probe_part_goes_on_a4_at_one_to_one():
    d = compose()
    assert (d.sheet, d.size_mm, d.scale, d.scale_value, d.scale_label) == ("A4", (297.0, 210.0), (1, 1), 1.0, "1:1")


def test_the_views_sit_where_the_hand_calculation_puts_them():
    v = by_name(compose())
    assert v["front"].origin == pytest.approx((75.0, 86.5), abs=1e-6)
    assert v["top"].origin == pytest.approx((75.0, 121.5), abs=1e-6)
    assert v["right"].origin == pytest.approx((190.0, 86.5), abs=1e-6)
    assert v["front"].extent == pytest.approx((100.0, 20.0), abs=1e-6)
    assert v["top"].extent == pytest.approx((100.0, 50.0), abs=1e-6)
    assert v["right"].extent == pytest.approx((50.0, 20.0), abs=1e-6)


def test_forced_one_to_two_halves_the_drawn_views_and_keeps_the_true_dimensions():
    d = compose(scale=S.parse_scale("1:2"))
    v = by_name(d)
    assert d.scale_value == 0.5 and d.dimlfac == 2.0
    assert v["front"].extent == pytest.approx((50.0, 10.0), abs=1e-6)
    assert v["top"].extent == pytest.approx((50.0, 25.0), abs=1e-6)
    assert v["right"].extent == pytest.approx((25.0, 10.0), abs=1e-6)
    assert sorted(x.text for x in d.dims) == ["100", "20", "50"]
    assert sorted(x.value_mm for x in d.dims) == pytest.approx([20.0, 50.0, 100.0])


def test_a_model_point_lands_on_origin_plus_scale_times_the_coordinate():
    v = by_name(compose(scale=S.parse_scale("1:2")))["front"]
    assert v.to_sheet((100.0, 20.0)) == pytest.approx((v.origin[0] + 50.0, v.origin[1] + 10.0), abs=1e-6)
    (x0, y0), (x1, y1) = v.box
    assert (x1 - x0, y1 - y0) == pytest.approx((50.0, 10.0), abs=1e-6)


# --------------------------------------------------------------------------- choosing sheet and scale


def test_a_five_millimetre_cube_is_enlarged_to_ten_to_one_on_a4():
    # 1:1 footprint: (8 + 5) + 15 + 5 = 33 square = 1089 mm^2, under a quarter of 50730 mm^2, so enlarge.
    # 10:1: (8 + 50) + 15 + 50 = 123 square, inside the 257 x 140 mm region above the title block
    d = compose(box=(5.0, 5.0, 5.0))
    assert (d.sheet, d.scale) == ("A4", (10, 1))
    assert d.scale_value == 10.0 and d.dimlfac == 0.1
    assert sorted(x.text for x in d.dims) == ["5", "5", "5"]


def test_enlargement_stops_at_the_largest_scale_that_fits():
    # 20 mm cube: 1:1 footprint 63 square = 3969 (< 12682); 10:1 is (8 + 200) + 15 + 200 = 423 wide, too wide; 5:1 is
    # (8 + 100) + 15 + 100 = 223 square, too tall for the 140 mm region; 2:1 is (8 + 40) + 15 + 40 = 103 square: fits
    d = compose(box=(20.0, 20.0, 20.0))
    assert (d.sheet, d.scale) == ("A4", (2, 1))


def test_a_long_plate_takes_a_larger_sheet_before_a_smaller_scale():
    # 1500 x 50 x 10 plate. 1:1 needs 8 + 1500 + 15 + 50 = 1573 mm, more than A0's 1149 mm of room.
    # 1:2 needs 8 + 750 + 15 + 25 = 798 mm: A2 has 554 mm, A1 has 841 - 20 - 10 - 2 * 5 = 801 mm
    d = compose(box=(1500.0, 50.0, 10.0))
    assert (d.sheet, d.scale) == ("A1", (1, 2))


def test_a_fixed_sheet_only_searches_the_scale():
    # on A4 (257 mm of room): 1:5 needs 8 + 300 + 15 + 10 = 333 mm, 1:10 needs 8 + 150 + 15 + 5 = 178 mm
    d = compose(box=(1500.0, 50.0, 10.0), sheet="A4")
    assert (d.sheet, d.scale) == ("A4", (1, 10))


def test_a_fixed_scale_only_searches_the_sheet():
    d = compose(box=(500.0, 100.0, 20.0), scale=S.parse_scale("1:1"))
    # 8 + 500 + 15 + 100 = 623 mm: A2 has 554 mm, A1 has 801 mm
    assert (d.sheet, d.scale) == ("A1", (1, 1))


def test_a_fixed_sheet_applies_the_quarter_rule_to_that_sheet():
    # the 5 mm cube on A3: footprint 1089 mm^2 is under a quarter of its 390 * 277 mm^2 frame, and 10:1 fits
    d = compose(box=(5.0, 5.0, 5.0), sheet="A3")
    assert (d.sheet, d.scale) == ("A3", (10, 1))


def test_nothing_fits_names_the_scale_or_the_sheet_that_would():
    # 500 x 100 x 20 on a fixed A4 at 1:1: 1:5 needs 8 + 100 + 15 + 20 = 143 mm and fits A4, A1 fits at 1:1
    e = failure(lambda: compose(box=(500.0, 100.0, 20.0), sheet="A4", scale=S.parse_scale("1:1")))
    assert e.family == "drawing.layout_failed"
    assert e.details["fits_at_scale"] == "1:5"
    assert e.details["fits_on_sheet"] == "A1"
    assert "1:5" in e.message and "A1" in e.message


def test_a_fixed_scale_that_fits_no_sheet_names_the_scale_that_fits_a0():
    e = failure(lambda: compose(box=(1500.0, 50.0, 10.0), scale=S.parse_scale("1:1")))
    assert e.family == "drawing.layout_failed"
    assert e.details["fits_at_scale"] == "1:2"
    assert "1:2" in e.message


def test_a_part_too_big_for_any_sheet_at_any_scale_fails():
    e = failure(lambda: compose(box=(300000.0, 50.0, 10.0)))
    assert e.family == "drawing.layout_failed"
    assert e.details["fits_at_scale"] is None


# --------------------------------------------------------------------------- arrangement of views


def test_third_angle_puts_top_above_front_and_right_to_the_right():
    v = by_name(compose(("front", "top", "right", "left", "bottom", "back")))
    front, top, bottom = v["front"].box, v["top"].box, v["bottom"].box
    assert top[0][1] > front[1][1] and bottom[1][1] < front[0][1]
    assert v["right"].box[0][0] > front[1][0] and v["left"].box[1][0] < front[0][0]
    assert v["back"].box[0][0] > v["right"].box[1][0]


def test_first_angle_mirrors_third_angle():
    v = by_name(compose(("front", "top", "right", "left", "bottom", "back"), method="first_angle"))
    front, top, bottom = v["front"].box, v["top"].box, v["bottom"].box
    assert top[1][1] < front[0][1] and bottom[0][1] > front[1][1]
    assert v["right"].box[1][0] < front[0][0] and v["left"].box[0][0] > front[1][0]
    assert v["back"].box[1][0] < v["right"].box[0][0]


def test_projected_views_line_up():
    v = by_name(compose())
    assert v["top"].box[0][0] == pytest.approx(v["front"].box[0][0], abs=1e-6)  # one column
    assert v["right"].box[0][1] == pytest.approx(v["front"].box[0][1], abs=1e-6)  # one row


def test_the_isometric_view_takes_the_free_top_right_area():
    v = by_name(compose(("front", "top", "right", "iso")))
    assert v["iso"].box[0][0] > v["top"].box[1][0] and v["iso"].box[0][1] > v["right"].box[1][1]


def test_in_first_angle_the_isometric_view_does_not_land_on_the_bottom_view():
    v = by_name(compose(("front", "bottom", "iso"), method="first_angle"))
    a, b = v["bottom"].box, v["iso"].box
    assert b[0][0] >= a[1][0] or b[1][0] <= a[0][0] or b[0][1] >= a[1][1] or b[1][1] <= a[0][1]


@pytest.mark.parametrize("method", ["third_angle", "first_angle"])
@pytest.mark.parametrize(
    "names",
    [("front",), ("top",), ("iso",), ("right", "front"), ("top", "front"), ("front", "iso"), ("back", "left", "bottom"), tuple(S.VIEW_NAMES)],
)
def test_any_subset_of_views_fits_without_overlap_and_inside_the_frame(names, method):
    d = compose(names, method=method)
    boxes = [v.box for v in d.views]
    for i, (a0, a1) in enumerate(boxes):
        for b0, b1 in boxes[i + 1 :]:
            assert a1[0] <= b0[0] or b1[0] <= a0[0] or a1[1] <= b0[1] or b1[1] <= a0[1], (a0, a1, b0, b1)
    x0, y0, x1, y1 = 20.0, 10.0, d.size_mm[0] - 10.0, d.size_mm[1] - 10.0
    tb_x = x1 - C.TITLE_BLOCK_WIDTH_MM
    for lo, hi in boxes:
        assert x0 < lo[0] and hi[0] < x1 and y0 < lo[1] and hi[1] < y1
        assert hi[0] <= tb_x or lo[1] >= y0 + C.TITLE_BLOCK_HEIGHT_MM
    for dim in d.dims:
        for px, py in (dim.p1, dim.p2, dim.base):
            assert x0 < px < x1 and y0 < py < y1


def test_notes_push_the_views_up_and_stay_above_the_title_block():
    plain, noted = compose(), compose(title=S.TitleBlock(notes=tuple(f"note {i}" for i in range(6))))
    assert by_name(noted)["top"].box[1][1] <= 200.0 - 5.0
    note_texts = [t for t in noted.texts if t.layer == "TEXT"]
    assert len(note_texts) == 6
    assert all(t.at[1] > 10.0 + C.TITLE_BLOCK_HEIGHT_MM for t in note_texts)
    assert note_texts[0].at[1] > note_texts[-1].at[1]  # note 1 on top
    assert note_texts[0].text.startswith("1. ") and note_texts[-1].text.startswith("6. ")
    assert by_name(noted)["front"].box[0][1] >= by_name(plain)["front"].box[0][1] - 1e-6


# --------------------------------------------------------------------------- dimensions


def dims(drawing) -> dict[str, S.PlacedDim]:
    return {d.source: d for d in drawing.dims}


def test_front_top_right_gives_x_and_z_on_the_front_and_y_on_the_top():
    d = compose()
    got = dims(d)
    assert set(got) == {"bbox:x", "bbox:y", "bbox:z"}
    assert (got["bbox:x"].view, got["bbox:x"].orientation, got["bbox:x"].text) == ("front", "horizontal", "100")
    assert (got["bbox:z"].view, got["bbox:z"].orientation, got["bbox:z"].text) == ("front", "vertical", "20")
    assert (got["bbox:y"].view, got["bbox:y"].orientation, got["bbox:y"].text) == ("top", "vertical", "50")
    assert [x.value_mm for x in d.dims] == pytest.approx([100.0, 50.0, 20.0])
    assert all(x.kind == "linear" for x in d.dims)


def test_each_axis_is_dimensioned_in_the_first_requested_view_that_shows_it():
    got = dims(compose(("top", "front", "right")))
    assert (got["bbox:x"].view, got["bbox:y"].view, got["bbox:z"].view) == ("top", "top", "front")


def test_a_single_front_view_gets_x_and_z_only():
    assert set(dims(compose(("front",)))) == {"bbox:x", "bbox:z"}


def test_an_isometric_view_gets_no_dimensions():
    assert compose(("iso",)).dims == ()


def test_dimension_mode_none_gives_no_dimensions_and_no_margin():
    with_dims, without = compose(), compose(dimensions="none")
    assert without.dims == ()
    assert by_name(without)["front"].origin != by_name(with_dims)["front"].origin


def test_dimension_geometry_is_a_row_outside_the_view():
    d = compose()
    front = by_name(d)["front"]
    (x0, y0), (x1, y1) = front.box
    horizontal, vertical = dims(d)["bbox:x"], dims(d)["bbox:z"]
    assert horizontal.p1 == pytest.approx((x0, y0)) and horizontal.p2 == pytest.approx((x1, y0))
    assert horizontal.base[1] == pytest.approx(y0 - 8.0)
    assert vertical.p1 == pytest.approx((x0, y0)) and vertical.p2 == pytest.approx((x0, y1))
    assert vertical.base[0] == pytest.approx(x0 - 8.0)


@pytest.mark.parametrize(("value", "text"), [(100.0, "100"), (20.0, "20"), (12.5, "12.5"), (0.25, "0.25"), (7.005, "7"), (99.999, "100"), (1234.5, "1234.5")])
def test_dimension_text_has_no_trailing_zeros(value, text):
    assert S.format_mm(value) == text


# --------------------------------------------------------------------------- title block and frame


def test_the_frame_is_20_mm_from_the_left_and_10_mm_from_the_others():
    frame = [line for line in compose().lines if line.layer == "FRAME"]
    xs = sorted({p[0] for line in frame for p in (line.a, line.b)})
    ys = sorted({p[1] for line in frame for p in (line.a, line.b)})
    assert (xs, ys) == ([20.0, 287.0], [10.0, 200.0])


def test_the_title_block_bands_add_up_to_its_height():
    assert sum(C.TITLE_BAND_HEIGHTS_MM) == C.TITLE_BLOCK_HEIGHT_MM
    assert all(sum(w for *_, w in band) == C.TITLE_BLOCK_WIDTH_MM for band in C.TITLE_BANDS)


def test_the_title_block_is_180_by_40_in_the_bottom_right_corner():
    box = [line for line in compose().lines if line.layer == "TITLE"]
    xs = [p[0] for line in box for p in (line.a, line.b)]
    ys = [p[1] for line in box for p in (line.a, line.b)]
    assert (min(xs), max(xs), min(ys), max(ys)) == (107.0, 287.0, 10.0, 50.0)


def test_the_title_block_texts_are_inside_it():
    title = S.TitleBlock(title="BRACKET", part_number="BR-001", material="AL 6061", revision="B", general_tolerance="ISO 2768-m", notes=())
    d = compose(title=title)
    texts = {t.text: t for t in d.texts}
    for text in ("BRACKET", "BR-001", "AL 6061", "B", "ISO 2768-m", "1:1", "A4", "THIRD ANGLE", C.STATUS_LINE):
        t = texts[text]
        assert 107.0 <= t.at[0] and 10.0 <= t.at[1] and t.at[1] + t.height <= 50.0, text
        assert t.at[0] + S.text_em(text) * t.height * t.width_factor <= 287.0 + 1e-6, text
    assert C.STATUS_LINE == "UNREVIEWED - generated by eng-toolkit, not engineering approved"


def test_first_angle_is_named_in_the_title_block():
    assert "FIRST ANGLE" in [t.text for t in compose(method="first_angle").texts]


def test_empty_fields_print_no_value_but_keep_their_label():
    texts = [t.text for t in compose().texts]
    assert "TITLE" in texts and "MATERIAL" in texts
    assert "BR-001" not in texts


def test_a_long_title_shrinks_first_and_a_wide_one_is_narrowed_with_a_warning():
    shrunk = compose(title=S.TitleBlock(title="X" * 80))
    t = next(t for t in shrunk.texts if t.text == "X" * 80)
    assert t.height < C.TITLE_VALUE_HEIGHT_MM and t.width_factor == 1.0
    assert shrunk.warnings == ()
    # 80 full-width characters are 80 text heights wide: even at the 2 mm minimum they need 160 of the 117 mm cell
    narrowed = compose(title=S.TitleBlock(title="零" * 80))
    t = next(t for t in narrowed.texts if t.text == "零" * 80)
    assert t.height == C.MIN_TEXT_HEIGHT_MM and 0.4 <= t.width_factor < 1.0
    assert any("title" in w for w in narrowed.warnings)


def test_fit_text_leaves_what_fits_and_shrinks_before_it_narrows():
    assert S.fit_text("ABC", 100.0, 5.0) == (5.0, 1.0, True)
    height, factor, fits = S.fit_text("A" * 40, 100.0, 5.0)
    assert (factor, fits) == (1.0, True) and height == pytest.approx(100.0 / (40 * C.LATIN_EM), abs=1e-3)
    height, factor, fits = S.fit_text("A" * 80, 60.0, 5.0)
    assert height == C.MIN_TEXT_HEIGHT_MM and 0.4 <= factor < 1.0 and fits
    assert S.fit_text("A" * 400, 60.0, 5.0)[2] is False


def test_wide_characters_count_a_full_em():
    assert S.text_em("零件") == pytest.approx(2.0)
    assert S.text_em("AB") == pytest.approx(2 * C.LATIN_EM)


# --------------------------------------------------------------------------- the caller's strings


@pytest.mark.parametrize("text", ["front,top,front", "front,spam", "", " , "])
def test_views_are_checked(text):
    e = failure(lambda: S.parse_views(text))
    assert (e.family, e.details["param"]) == ("input.invalid", "views")


def test_views_are_normalised():
    assert S.parse_views("Front, TOP ") == ("front", "top")
    assert S.parse_views(",".join(S.VIEW_NAMES)) == S.VIEW_NAMES


@pytest.mark.parametrize("fn,text", [(S.parse_sheet, "A5"), (S.parse_sheet, "letter"), (S.parse_scale, "1:3"), (S.parse_scale, "0.5"), (S.parse_method, "second_angle"), (S.parse_dimensions, "all")])
def test_unknown_choices_are_input_invalid_and_name_the_allowed_ones(fn, text):
    e = failure(lambda: fn(text))
    assert e.family == "input.invalid" and e.details["allowed"]


def test_known_choices_are_accepted():
    assert S.parse_sheet("a3") == "A3" and S.parse_sheet("AUTO") == "auto"
    assert S.parse_scale("1:2") == (1, 2) and S.parse_scale("10:1") == (10, 1) and S.parse_scale("Auto") == "auto"
    assert S.parse_method("First_Angle") == "first_angle"
    assert S.parse_dimensions("auto") == "auto" and S.parse_dimensions("overall") == "overall" and S.parse_dimensions("none") == "none"


def test_free_text_is_limited_printable_and_may_be_non_ascii():
    assert S.clean_text("title", "支架 é Ø10", 80) == "支架 é Ø10"
    assert failure(lambda: S.clean_text("title", "x" * 81, 80)).details["limit"] == 80
    for bad in ("a\nb", "a\tb", "a\x00b", "a\x1bb", "a b"):
        assert failure(lambda bad=bad: S.clean_text("title", bad, 80)).family == "input.invalid"


def test_notes_are_split_on_semicolons_and_limited():
    assert S.parse_notes("") == ()
    assert S.parse_notes("deburr; break edges 0.5 ;") == ("deburr", "break edges 0.5")
    assert failure(lambda: S.parse_notes(";".join("n" * 10 for _ in range(7)))).details["limit"] == 6
    assert failure(lambda: S.parse_notes("n" * 101)).details["limit"] == 100
    assert len(S.parse_notes(";".join("n" * 100 for _ in range(6)))) == 6


# --------------------------------------------------------------------------- hole callouts and ordinates (slice S6)


def hole_plan(names=("front", "top", "right"), at=(25.0, 25.0, 20.0)):
    from eng_toolkit.brep import HoleInfo
    from eng_toolkit.domains.drawing import callouts as K

    hole = HoleInfo(id="H1", face_ids=(1,), diameter_mm=10.0, axis=(0.0, 0.0, -1.0), entry_mm=at, depth_mm=20.0, thru=True)
    return K.plan(K.group_holes([hole]), views(names)).views


def test_a_hole_callout_takes_a_margin_to_the_right_of_its_view_and_the_ordinates_two_rows():
    """Probe part with the Ø10 hole at (25, 25), callout "Ø10 THRU" (8 characters x 0.62 x 3.5 mm = 17.36 mm of text).

    margin to the right of the top view's column: gap 6 + 17.36 + air 2 = 25.36.  Column 0: 16 mm to its left (the top view
    has the Y dimension and the y ordinates), 100 mm, 25.36, gap 15; column 1: 50 mm  ->  16 + 100 + 25.36 + 15 + 50 = 206.36.
    Rows: front row 8 (X dimension) + 20, gap 15, top row 8 (x ordinates) + 50  ->  8 + 20 + 15 + 8 + 50 = 101.
    Corner ((25 + 282 - 206.36) / 2, (55 + 195 - 101) / 2) = (50.32, 74.5).
    """
    d = compose(holes=hole_plan())
    v = by_name(d)
    assert (d.sheet, d.scale) == ("A4", (1, 1))
    assert v["front"].origin == pytest.approx((50.32 + 16.0, 74.5 + 8.0), abs=1e-4)
    assert v["top"].origin == pytest.approx((50.32 + 16.0, 74.5 + 8.0 + 20.0 + 15.0 + 8.0), abs=1e-4)
    assert v["right"].origin == pytest.approx((50.32 + 16.0 + 100.0 + 25.36 + 15.0, 74.5 + 8.0), abs=1e-4)


def test_the_ordinates_sit_in_the_rows_nearest_the_view_and_the_overall_dimension_moves_out():
    d = compose(holes=hole_plan())
    (x0, y0), _ = by_name(d)["top"].box
    assert (x0, y0) == pytest.approx((66.32, 125.5), abs=1e-4)
    xs = sorted((o.value_mm, o.feature, o.end) for o in d.ordinates if o.axis == "x")
    assert [(value, f, e) for value, f, e in xs] == [
        (0.0, pytest.approx((x0, y0), abs=1e-4), pytest.approx((x0, y0 - 8.0), abs=1e-4)),
        (25.0, pytest.approx((x0 + 25.0, y0 + 25.0), abs=1e-4), pytest.approx((x0 + 25.0, y0 - 8.0), abs=1e-4)),
    ]
    ys = sorted((o.value_mm, o.feature, o.end) for o in d.ordinates if o.axis == "y")
    assert [(value, f, e) for value, f, e in ys] == [
        (0.0, pytest.approx((x0, y0), abs=1e-4), pytest.approx((x0 - 8.0, y0), abs=1e-4)),
        (25.0, pytest.approx((x0 + 25.0, y0 + 25.0), abs=1e-4), pytest.approx((x0 - 8.0, y0 + 25.0), abs=1e-4)),
    ]
    assert all(o.origin == pytest.approx((x0, y0), abs=1e-4) for o in d.ordinates)
    (y_dim,) = [dim for dim in d.dims if dim.source == "bbox:y"]
    assert y_dim.base[0] == pytest.approx(x0 - 16.0, abs=1e-4)  # the second column to the left of the top view
    (x_dim,) = [dim for dim in d.dims if dim.source == "bbox:x"]
    assert x_dim.view == "front" and x_dim.base[1] == pytest.approx(by_name(d)["front"].box[0][1] - 8.0, abs=1e-4)  # the front view has no ordinates


def test_the_callout_text_starts_6_mm_right_of_the_view_at_the_height_of_its_hole():
    d = compose(holes=hole_plan())
    (x0, y0), (x1, _) = by_name(d)["top"].box
    (c,) = d.callouts
    centre = (x0 + 25.0, y0 + 25.0)
    assert (c.view, c.text, c.height) == ("top", "Ø10 THRU", 3.5)
    assert c.center == pytest.approx(centre, abs=1e-4)
    assert c.text_at == pytest.approx((x1 + 6.0, centre[1] - 1.75), abs=1e-4)
    start, end = c.leader
    assert end == pytest.approx((x1 + 5.0, centre[1]), abs=1e-4) and start == pytest.approx((centre[0] + 5.0, centre[1]), abs=1e-4)  # from the edge of the R5 circle
    assert [t.text for t in d.texts if t.layer == "DIM"] == ["Ø10 THRU"]


def test_two_callouts_of_one_view_are_stacked_six_millimetres_apart():
    from eng_toolkit.brep import HoleInfo
    from eng_toolkit.domains.drawing import callouts as K

    holes = [
        HoleInfo(id="H1", face_ids=(1,), diameter_mm=6.0, axis=(0.0, 0.0, -1.0), entry_mm=(20.0, 40.0, 20.0), depth_mm=20.0, thru=True),
        HoleInfo(id="H2", face_ids=(2,), diameter_mm=8.0, axis=(0.0, 0.0, -1.0), entry_mm=(60.0, 42.0, 20.0), depth_mm=20.0, thru=True),
    ]
    d = compose(holes=K.plan(K.group_holes(holes), views(("front", "top", "right"))).views)
    first, second = sorted(d.callouts, key=lambda c: -c.text_at[1])
    assert (first.text, second.text) == ("Ø8 THRU", "Ø6 THRU")  # the higher hole (y = 42) first
    assert first.text_at[1] == pytest.approx(first.center[1] - 1.75, abs=1e-4)
    assert second.leader[1][1] == pytest.approx(first.leader[1][1] - 6.0, abs=1e-4)  # 2 mm lower than its hole would be: pushed down to keep 6 mm


def test_the_views_without_holes_are_laid_out_as_before():
    plain = compose()
    with_empty_plan = compose(holes=())
    assert plain == with_empty_plan and plain.ordinates == () and plain.callouts == ()


def test_ordinates_too_close_together_are_warned_not_moved():
    from eng_toolkit.brep import HoleInfo
    from eng_toolkit.domains.drawing import callouts as K

    holes = [HoleInfo(id=f"H{i}", face_ids=(i,), diameter_mm=1.0, axis=(0.0, 0.0, -1.0), entry_mm=(30.0 + 2.0 * i, 25.0, 20.0), depth_mm=20.0, thru=True) for i in range(1, 4)]
    d = compose(holes=K.plan(K.group_holes(holes), views(("front", "top", "right"))).views)
    assert any("x ordinates" in w and "overlap" in w for w in d.warnings)
