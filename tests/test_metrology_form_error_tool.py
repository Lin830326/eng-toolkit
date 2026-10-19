"""metrology_form_error as a tool: point input (inline and file), units, error families, the result contract.

The numbers are the closed-form cases of tests/test_metrology_form.py:

    six points alternately at R = 10.1 and 9.9 mm, 60 degrees apart      -> circularity 0.2 mm
    6 x 6 grid tilted 20 degrees, every other point +-0.05 mm off plane  -> flatness 0.10 mm
    circles of radius 10 (z = 0) and 10.3 (z = 30), 24 points each       -> cylindricity 0.3 mm
"""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.metrology.gdt_tools import form_error

LOBE6 = [
    [10.1 * math.cos(k * math.pi / 3), 10.1 * math.sin(k * math.pi / 3)] if k % 2 == 0
    else [9.9 * math.cos(k * math.pi / 3), 9.9 * math.sin(k * math.pi / 3)]
    for k in range(6)
]


def _tilted_grid(angle=20.0):
    th = math.radians(angle)
    normal = np.array([math.sin(th), 0.0, math.cos(th)])
    e1 = np.array([math.cos(th), 0.0, -math.sin(th)])
    pts = []
    for i in range(6):
        for j in range(6):
            off = 0.05 if (6 * i + j) % 2 == 0 else -0.05
            pts.append(i * 10 * e1 + np.array([0.0, j * 10.0, 0.0]) + off * normal)
    return np.array(pts)


def _cone_points():
    rows = []
    for z, r in ((0.0, 10.0), (30.0, 10.3)):
        for k in range(24):
            a = k * 2 * math.pi / 24
            rows.append([r * math.cos(a), r * math.sin(a), z])
    return np.array(rows)


def _call(**kwargs) -> ToolResult:
    fn = getattr(form_error, "fn", form_error)
    result = fn(**kwargs)
    assert isinstance(result, ToolResult)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    assert result.review_status == "unreviewed"
    return result


def _failed(**kwargs) -> tuple[str, str]:
    r = _call(**kwargs)
    assert not r.ok, r.value
    assert r.warnings == []
    return r.error.family, r.error.message


# --------------------------------------------------------------------------- results


def test_circularity_of_the_lobed_points_inline_json():
    r = _call(feature="circularity", points=json.dumps(LOBE6))
    assert r.ok, r.error
    assert r.value.quantity == pytest.approx(0.2, abs=1e-9)
    assert r.value.unit == "millimeter"
    assert r.values["form_error"] == r.value
    assert r.values["points_used"].quantity == 6
    m = r.data["method"]
    assert m["exact"] is True and m["first_order_optimal"] is True and m["is_upper_bound_of_minimum_zone"] is True
    assert "minimum zone" in m["estimator"] and "exhaustive" in m["search"]
    assert r.data["zone"]["centre"] == pytest.approx([0.0, 0.0], abs=1e-9)
    assert r.provenance.engine_versions["numpy"]


def test_text_rows_with_semicolons_equal_json():
    text = "; ".join(f"{x!r},{y!r}" for x, y in LOBE6)
    a = _call(feature="circularity", points=text)
    b = _call(feature="circularity", points=json.dumps(LOBE6))
    assert a.value.quantity == b.value.quantity


def test_flatness_of_the_tilted_grid_and_the_method_it_reports():
    r = _call(feature="flatness", points=json.dumps(_tilted_grid().tolist()))
    assert r.value.quantity == pytest.approx(0.10, abs=1e-9)
    assert r.values["least_squares_form_error"].quantity >= r.value.quantity - 1e-12
    assert r.data["method"]["exact"] is False  # 36 points: polished, optimality-checked, not exhaustive
    assert r.data["method"]["first_order_optimal"] is True
    assert any("upper bound" in w for w in r.warnings)


def test_straightness_three_points():
    r = _call(feature="straightness", points="0,0; 10,0.3; 20,0")
    assert r.value.quantity == pytest.approx(0.3, abs=1e-12)
    assert r.data["method"]["exact"] is True


def test_cylindricity_is_never_claimed_exact():
    r = _call(feature="cylindricity", points=json.dumps(_cone_points().tolist()))
    assert r.value.quantity == pytest.approx(0.3, abs=1e-7)
    assert r.data["method"]["exact"] is False
    assert "not proven minimal" in r.data["method"]["estimator"]
    assert "least_squares_form_error" not in r.values
    assert any("upper bound" in w for w in r.warnings)


def test_units_mm_inch_micrometre_and_output_unit():
    pts = np.array(LOBE6)
    mm = _call(feature="circularity", points=json.dumps(pts.tolist()))
    inch = _call(feature="circularity", points=json.dumps((pts / 25.4).tolist()), unit="inch")
    um = _call(feature="circularity", points=json.dumps((pts * 1000).tolist()), unit="um")
    for r in (mm, inch, um):
        assert r.value.quantity == pytest.approx(0.2, abs=1e-9)
    out = _call(feature="circularity", points=json.dumps(pts.tolist()), output_unit="um")
    assert out.value.unit == "micrometer"
    assert out.value.quantity == pytest.approx(200.0, abs=1e-6)
    inch_out = _call(feature="circularity", points=json.dumps(pts.tolist()), output_unit="inch")
    assert inch_out.value.quantity == pytest.approx(0.2 / 25.4, rel=1e-9)


def test_huge_coordinates_report_a_value_at_the_data_resolution():
    a = np.linspace(0, 2 * math.pi, 41)[:-1]
    pts = np.c_[1e6 + 10 * np.cos(a), 1e6 + 10 * np.sin(a)]
    r = _call(feature="circularity", points=json.dumps(pts.tolist()))
    assert r.ok
    assert r.value.quantity < 1e-9
    assert r.data["points"]["coordinate_resolution_mm"] == pytest.approx(1.16e-10, rel=0.05)
    assert any("resolution" in w for w in r.warnings)


def test_three_dimensional_section_is_projected_onto_its_plane():
    th = math.radians(35)
    rot = np.array([[1, 0, 0], [0, math.cos(th), -math.sin(th)], [0, math.sin(th), math.cos(th)]])
    flat = np.array([[x, y, 0.0] for x, y in LOBE6])
    r = _call(feature="circularity", points=json.dumps((flat @ rot.T + [7, 8, 9]).tolist()))
    assert r.value.quantity == pytest.approx(0.2, abs=1e-9)
    assert r.data["out_of_plane_spread_mm"] < 1e-9
    bent = flat.copy()
    bent[:, 2] = [0, 0.5, 0, 0.5, 0, 0.5]
    r2 = _call(feature="circularity", points=json.dumps(bent.tolist()))
    assert any("not in one plane" in w for w in r2.warnings)


def test_duplicates_are_dropped_and_said():
    pts = LOBE6 + LOBE6[:2]
    r = _call(feature="circularity", points=json.dumps(pts))
    assert r.value.quantity == pytest.approx(0.2, abs=1e-9)
    assert r.data["points"] == {"given": 8, "distinct": 6, "unit": "mm", "coordinate_resolution_mm": r.data["points"]["coordinate_resolution_mm"]}
    assert any("2 duplicate" in w for w in r.warnings)


# --------------------------------------------------------------------------- tolerance check


def test_tolerance_pass_and_fail_with_a_proven_minimum():
    ok = _call(feature="circularity", points=json.dumps(LOBE6), tolerance="0.25 mm")
    assert ok.data["tolerance_check"]["within_tolerance"] is True
    assert ok.data["tolerance_check"]["margin_mm"] == pytest.approx(0.05, abs=1e-9)
    bad = _call(feature="circularity", points=json.dumps(LOBE6), tolerance="0.15 mm")
    chk = bad.data["tolerance_check"]
    assert chk["within_tolerance"] is False and chk["conclusive"] is True
    edge = _call(feature="circularity", points=json.dumps(LOBE6), tolerance="0.2 mm")
    assert edge.data["tolerance_check"]["within_tolerance"] is True  # exactly on the limit


def test_a_fail_from_an_unproven_zone_is_not_conclusive():
    r = _call(feature="cylindricity", points=json.dumps(_cone_points().tolist()), tolerance="0.2 mm")
    chk = r.data["tolerance_check"]
    assert chk["within_tolerance"] is False and chk["conclusive"] is False
    ok = _call(feature="cylindricity", points=json.dumps(_cone_points().tolist()), tolerance="0.4 mm")
    assert ok.data["tolerance_check"]["within_tolerance"] is True and ok.data["tolerance_check"]["conclusive"] is True


# --------------------------------------------------------------------------- files


def test_csv_file_with_header_and_extra_columns(tmp_path):
    f = tmp_path / "section.csv"
    rows = ["id,x,y,probe"] + [f"{i},{x!r},{y!r},9" for i, (x, y) in enumerate(LOBE6)]
    f.write_text("\r\n".join(rows) + "\r\n", encoding="utf-8")
    r = _call(feature="circularity", points_path=str(f))
    assert r.ok, r.error
    assert r.value.quantity == pytest.approx(0.2, abs=1e-9)
    assert len(r.provenance.input_files) == 1
    ref = r.provenance.input_files[0]
    assert Path(ref.path).name == "section.csv" and ref.bytes == f.stat().st_size and len(ref.sha256) == 64
    assert (Path(r.data["job_dir"]) / "input" / "1-section.csv").is_file()  # the tool read the copy that was checked


def test_whitespace_xyz_file_with_comment_and_no_header(tmp_path):
    f = tmp_path / "plane.xyz"
    f.write_text("# CMM export\n" + "\n".join(f"{x} {y} {z}" for x, y, z in _tilted_grid().tolist()), encoding="utf-8")
    r = _call(feature="flatness", points_path=str(f))
    assert r.value.quantity == pytest.approx(0.10, abs=1e-9)


def test_file_errors(tmp_path):
    assert _failed(feature="flatness", points_path=str(tmp_path / "none.csv"))[0] == "input.file_not_found"
    img = tmp_path / "p.png"
    img.write_bytes(b"x")
    assert _failed(feature="flatness", points_path=str(img))[0] == "input.unsupported_format"
    binary = tmp_path / "b.csv"
    binary.write_bytes(b"\xff\xfe\x00\x01\x02")
    assert _failed(feature="flatness", points_path=str(binary))[0] == "input.invalid"


def test_points_path_obeys_the_read_directories(tmp_path, monkeypatch):
    allowed = tmp_path / "ok"
    allowed.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_text("0,0\n1,1\n2,0\n", encoding="utf-8")
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(allowed))
    assert _failed(feature="straightness", points_path=str(outside))[0] == "input.path_not_allowed"


# --------------------------------------------------------------------------- errors


def test_exactly_one_source():
    assert _failed(feature="flatness")[0] == "input.invalid"
    assert _failed(feature="flatness", points="0,0,0", points_path="x.csv")[0] == "input.invalid"


def test_bad_feature_unit_and_tolerance():
    assert _failed(feature="roundness", points="0,0")[0] == "input.invalid"
    assert _failed(feature="straightness", points="0,0;1,0;2,0", unit="2 mm")[0] == "input.invalid"
    assert _failed(feature="straightness", points="0,0;1,0;2,0", unit="s")[0] == "units.dimension_mismatch"
    assert _failed(feature="straightness", points="0,0;1,0;2,0", tolerance="0.01")[0] == "units.bare_number"
    assert _failed(feature="straightness", points="0,0;1,0;2,0", output_unit="kg")[0].startswith("units.")


def test_wrong_shapes_name_the_row_and_the_columns():
    family, msg = _failed(feature="flatness", points="0,0;1,0;0,1;1,1")
    assert family == "input.invalid" and "3 are needed" in msg
    family, msg = _failed(feature="straightness", points="0,0,0;1,0,0;2,0,0")
    assert family == "input.invalid" and "2 are needed" in msg
    family, msg = _failed(feature="straightness", points="0,0;1,0,1;2,0")
    assert family == "input.invalid" and "row 2" in msg
    assert _failed(feature="straightness", points="[[0,0],[1,0],[2]]")[0] == "input.invalid"
    assert _failed(feature="straightness", points="[1, 2, 3]")[0] == "input.invalid"
    assert _failed(feature="straightness", points="[[0,0],[1,0]")[0] == "input.invalid"  # broken JSON
    assert _failed(feature="straightness", points="{\"a\": 1}")[0] == "input.invalid"


def test_nan_inf_and_text_values():
    for bad, where in (("0,0;1,nan;2,0", "row 2"), ("0,0;inf,1;2,0", "row 2"), ("0,0;1,0;2,x", "row 3")):
        family, msg = _failed(feature="straightness", points=bad)
        assert family == "input.invalid" and where in msg, msg
    family, msg = _failed(feature="straightness", points="[[0,0],[1,NaN],[2,0]]")
    assert family == "input.invalid" and "row 2" in msg
    assert _failed(feature="straightness", points="[[0,0],[1,true],[2,0]]")[0] == "input.invalid"
    assert _failed(feature="straightness", points='[[0,0],[1,"1_0"],[2,0]]')[0] == "input.invalid"


def test_too_few_points_and_duplicates():
    family, msg = _failed(feature="flatness", points="0,0,0;1,0,0;0,1,0")
    assert family == "input.too_few_points" and "at least 4" in msg
    family, msg = _failed(feature="circularity", points="0,0;1,0;0,1;0,0;1,0;0,1;0,0")
    assert family == "input.too_few_points" and "4 duplicates" in msg
    assert _failed(feature="cylindricity", points="0,0,0;1,0,0;0,1,0;0,0,1;1,1,1")[0] == "input.too_few_points"


def test_degenerate_geometry():
    assert _failed(feature="flatness", points="0,0,0;1,1,1;2,2,2;3,3,3")[0] == "geometry.degenerate"
    assert _failed(feature="circularity", points="0,0;1,1;2,2;3,3;4,4")[0] == "geometry.degenerate"
    family, msg = _failed(feature="cylindricity", points="1,0,0;0,1,0;-1,0,0;0,-1,0;0.7,0.7,0;-0.7,0.7,0")
    assert family == "geometry.degenerate" and "two cross-sections" in msg


def test_too_many_points_for_cylindricity():
    rows = "\n".join(f"{math.cos(i)},{math.sin(i)},{i}" for i in range(100_001))
    family, msg = _failed(feature="cylindricity", points=rows)
    assert family == "input.too_large"
