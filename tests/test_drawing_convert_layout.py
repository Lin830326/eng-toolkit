"""The page, the scale and the strings of drawing_convert: pure functions, every expected number worked out by hand.

Usable area of a named sheet = sheet - 2 x 10 mm margins: A4 277 x 190 (portrait 190 x 277), A3 400 x 277, A0 1169 x 821.
"""

import math

import pytest

from eng_toolkit.domains.drawing import convert as V
from eng_toolkit.errors import ToolFailure


def page(w, h, sheet="auto", ratio=None):
    return V.choose_page(w, h, sheet, ratio)


# --------------------------------------------------------------------------- an automatic sheet: extents + 10 mm on each side


def test_auto_sheet_and_scale_is_true_size_with_10_mm_all_round():
    p = page(100.0, 0.0)
    assert (p.sheet, p.ratio, p.orientation) == ("auto", (1, 1), "custom")
    assert (p.width_mm, p.height_mm) == (120.0, 20.0)


def test_auto_sheet_with_a_given_scale_scales_the_extents_before_adding_the_margins():
    assert (page(100.0, 50.0, ratio=(1, 2)).width_mm, page(100.0, 50.0, ratio=(1, 2)).height_mm) == (70.0, 45.0)  # 50 + 20, 25 + 20
    assert (page(100.0, 50.0, ratio=(2, 1)).width_mm, page(100.0, 50.0, ratio=(2, 1)).height_mm) == (220.0, 120.0)  # 200 + 20, 100 + 20


def test_auto_scale_on_an_auto_sheet_reduces_only_when_the_page_would_pass_a0():
    assert page(1169.0, 821.0).ratio == (1, 1)  # exactly the usable area of A0
    p = page(2000.0, 1000.0)  # 1:2 gives 1000 x 500, which fits; 1:1 does not
    assert p.ratio == (1, 2) and (p.width_mm, p.height_mm) == (1020.0, 520.0)
    assert page(100000.0, 100.0).ratio == (1, 100)  # 1:100 gives 1000 x 1, which fits A0


def test_a_page_over_the_pdf_limit_is_refused_with_the_size_it_would_have():
    with pytest.raises(ToolFailure) as e:
        page(6000.0, 100.0, ratio=(1, 1))  # 6020 mm
    assert e.value.family == "input.out_of_range"
    assert e.value.details["value_mm"] == pytest.approx(6020.0) and e.value.details["limit_mm"] == V.LIMITS.max_page_mm
    with pytest.raises(ToolFailure) as e:
        page(1e9, 10.0)  # even 1:10000 gives 100000 mm
    assert e.value.family == "input.out_of_range"


# --------------------------------------------------------------------------- a named sheet


def test_a_named_sheet_takes_the_largest_scale_that_fits_and_never_enlarges():
    assert page(400.0, 250.0, "A3").ratio == (1, 1)  # 400 x 277 usable: exactly fits
    p = page(500.0, 250.0, "A3")  # 1:2 gives 250 x 125
    assert p.ratio == (1, 2) and (p.width_mm, p.height_mm, p.orientation) == (420.0, 297.0, "landscape")
    assert page(10.0, 10.0, "A3").ratio == (1, 1)  # a small part stays true size
    assert page(1000.0, 100.0, "A4").ratio == (1, 5)  # 1:2 gives 500 > 277; 1:5 gives 200 x 20


def test_a_part_taller_than_wide_takes_the_sheet_upright_when_only_that_fits():
    p = page(150.0, 250.0, "A4")  # landscape usable 277 x 190: 250 > 190; portrait 190 x 277: fits
    assert (p.orientation, p.width_mm, p.height_mm, p.ratio) == ("portrait", 210.0, 297.0, (1, 1))
    assert page(250.0, 150.0, "A4").orientation == "landscape"
    assert page(190.0, 190.0, "A4").orientation == "landscape"  # fits both ways: landscape is preferred


def test_a_given_scale_that_does_not_fit_the_sheet_is_refused_not_clipped():
    with pytest.raises(ToolFailure) as e:
        page(1000.0, 100.0, "A4", (1, 1))
    assert e.value.family == "drawing.layout_failed" and e.value.details["sheet"] == "A4"
    assert "277" in e.value.message  # says what does fit
    ok = page(1000.0, 100.0, "A4", (1, 5))
    assert (ok.width_mm, ok.height_mm) == (297.0, 210.0)


def test_nothing_fits_even_at_the_smallest_scale():
    with pytest.raises(ToolFailure) as e:
        page(1e7, 1e7, "A4")
    assert e.value.family == "drawing.layout_failed"


def test_a_non_finite_extent_is_refused():
    for bad in (math.inf, math.nan, -1.0):
        with pytest.raises(ToolFailure) as e:
            page(bad, 10.0)
        assert e.value.family == "input.out_of_range"


def test_the_factor_is_mm_per_unit_times_the_scale():
    p = page(100.0, 100.0, ratio=(1, 2))
    assert V.factor(25.4, p) == pytest.approx(12.7)  # an inch drawing at 1:2: 25.4 / 2 mm on the page per inch
    assert V.factor(1.0, page(1.0, 1.0, ratio=(2, 1))) == 2.0


# --------------------------------------------------------------------------- the strings


@pytest.mark.parametrize(("text", "expected"), [("auto", None), (" AUTO ", None), ("1:1", (1, 1)), ("1 : 2", (1, 2)), ("2:1", (2, 1)), ("1:10000", (1, 10000)), ("50:1", (50, 1))])
def test_scales_that_are_iso_5455(text, expected):
    assert V.parse_scale(text) == expected


@pytest.mark.parametrize("text", ["1:3", "0:1", "1:0", "abc", "", "1/2", "3:1", "1:1e3", "-1:1"])
def test_scales_that_are_not_are_refused_with_the_allowed_list(text):
    with pytest.raises(ToolFailure) as e:
        V.parse_scale(text)
    assert e.value.family == "input.invalid" and e.value.details["param"] == "/scale" and "auto" in e.value.details["allowed"]


@pytest.mark.parametrize(("text", "fmt"), [("pdf", "pdf"), ("PDF", "pdf"), (".svg", "svg"), (" png ", "png"), ("dxf", "dxf")])
def test_output_formats(text, fmt):
    assert V.parse_format(text) == fmt


@pytest.mark.parametrize("text", ["", "docx", "dwg", "pdf,svg", "stp"])
def test_unknown_output_formats_are_refused(text):
    with pytest.raises(ToolFailure) as e:
        V.parse_format(text)
    assert e.value.family == "input.invalid" and e.value.details["allowed"] == ["pdf", "svg", "png", "dxf"]


@pytest.mark.parametrize(("text", "name"), [("R12", "R12"), ("r2000", "R2000"), (" R2018 ", "R2018"), ("R2007", "R2007")])
def test_dxf_versions(text, name):
    assert V.parse_dxf_version(text) == name


@pytest.mark.parametrize("text", ["R14", "R13", "R2019", "AC1032", "", "12", "R2005"])
def test_dxf_versions_that_cannot_be_written_are_refused(text):
    with pytest.raises(ToolFailure) as e:
        V.parse_dxf_version(text)
    assert e.value.family == "input.invalid" and e.value.details["allowed"] == list(V.DXF_VERSIONS)


def test_the_release_codes_are_the_ones_the_dxf_reference_gives():
    assert V.DXF_VERSIONS == {"R12": "AC1009", "R2000": "AC1015", "R2004": "AC1018", "R2007": "AC1021", "R2010": "AC1024", "R2013": "AC1027", "R2018": "AC1032"}


def test_the_work_counts_an_entity_a_vertex_and_five_per_character():
    assert V.weigh(10, 20, 3) == 10 + 20 + 15


def test_runtime_limits_follow_the_environment(monkeypatch):
    limits, timeout, memory = V.runtime_limits(30.0, 2048)
    assert (limits.max_file_bytes, timeout, memory) == (50_000_000, 60.0, 2048)
    monkeypatch.setenv(V.ENV_MAX_MB, "1")
    monkeypatch.setenv(V.ENV_TIMEOUT, "7")
    monkeypatch.setenv(V.ENV_MEMORY, "512")
    limits, timeout, memory = V.runtime_limits(30.0, 2048)
    assert (limits.max_file_bytes, timeout, memory) == (1_000_000, 7.0, 512)
    monkeypatch.setenv(V.ENV_TIMEOUT, "-3")  # nonsense falls back to the default
    monkeypatch.setenv(V.ENV_MEMORY, "nan")
    assert V.runtime_limits(30.0, 2048)[1:] == (60.0, 2048)
