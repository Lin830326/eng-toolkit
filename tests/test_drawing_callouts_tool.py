"""drawing_make_views, part 2 (slice S6): hole callouts, ordinates and the features file, end to end.

Probe part: 100 x 50 x 20 block, a vertical diameter-10 through hole at (25, 25), R2 fillets on the four outer vertical
edges.  In the top view (u = X 0..100, v = Y 0..50, datum = lower left corner) the hole is at (25, 25), so the callout
is ``Ø10 THRU`` and the ordinates are x = 0, 25 and y = 0, 25.

Plate part: the same block with four diameter-6.6 through holes at (10, 10), (90, 10), (10, 40), (90, 40) and a blind
diameter-5 hole 8 mm deep from the top at (50, 25): callouts ``4× Ø6.6 THRU`` and ``Ø5 DEEP 8``, ordinates x = 0, 10, 50,
90 and y = 0, 10, 25, 40.

Every number is worked out here from those coordinates.  The DXF is read back by tests/dxf_reader.py (group codes) and by
drawing_read, neither of which the writer uses.
"""

import hashlib
import json
import math
import shutil
from pathlib import Path

import dxf_reader as R
import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.drawing import sheet as S
from eng_toolkit.domains.drawing.tools import make_views
from eng_toolkit.domains.drawing.tools import read as read_tool
from eng_toolkit.domains.geometry.part import EXAMPLE_SPEC
from eng_toolkit.domains.geometry.tools import make_part

pytest.importorskip("ezdxf", reason="ezdxf is not installed; run: uv sync --extra drawing")
pytest.importorskip("pdfminer", reason="pdfminer.six is not installed; run: uv sync --extra drawing")

DIA = "Ø"
TIMES = "×"


@pytest.fixture(scope="module")
def models(tmp_path_factory):
    pytest.importorskip("OCP", reason="OCP is not installed; run: uv sync --extra cad")
    import cad_fixtures as cad

    root = tmp_path_factory.mktemp("callout_models")
    plate = cad.cut(
        cad.box(),
        *(cad.cylinder(x, y, -1.0, 3.3, 22.0) for x, y in ((10, 10), (90, 10), (10, 40), (90, 40))),
        cad.cylinder(50, 25, 12.0, 2.5, 9.0),  # blind: from z = 12 up through the top face at z = 20
    )
    return {
        "probe": cad.write_step(cad.probe_part(), root / "probe.step"),
        "plate": cad.write_step(plate, root / "plate.step"),
        "plain": cad.write_step(cad.box(), root / "plain.step"),
        "faces": cad.write_iges_faces(cad.probe_part(), root / "faces.iges"),
    }


@pytest.fixture(scope="module")
def made_part(tmp_path_factory):
    """geometry_make_part's own output for the example spec (a diameter-10 H7 hole at (25, 25)): the STEP and its features.json."""
    pytest.importorskip("OCP", reason="OCP is not installed; run: uv sync --extra cad")
    result = getattr(make_part, "fn", make_part)(spec=json.dumps(EXAMPLE_SPEC), name="bracket")
    assert result.ok, result.error
    found = {Path(a.path).name: Path(a.path) for a in result.artifacts}
    return {"step": found["bracket.step"], "features": found["bracket.features.json"]}


def run(model, **kwargs) -> ToolResult:
    result = getattr(make_views, "fn", make_views)(model_path=str(model), **kwargs)
    assert isinstance(result, ToolResult)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    return result


def artifact(result: ToolResult, name: str) -> Path:
    return next(Path(a.path) for a in result.artifacts if Path(a.path).name == name)


def dxf_of(result: ToolResult) -> R.Dxf:
    return R.read(artifact(result, "drawing.dxf"))


def texts(result: ToolResult) -> list[str]:
    return [e.text for e in dxf_of(result).of("TEXT")]


def ordinates(result: ToolResult) -> list[tuple[str, float]]:
    """(axis, true value) of every ordinate DIMENSION, from the raw group codes: |feature - datum| x the entity's DIMLFAC."""
    out = []
    for e in dxf_of(result).of("DIMENSION"):
        if e.dim_kind != 6:
            continue
        origin, feature = e.point(10, 20), e.point(13, 23)
        x_type = bool(int(e.get(70)) & 64)
        value = abs(feature[0] - origin[0]) if x_type else abs(feature[1] - origin[1])
        out.append(("x" if x_type else "y", round(value * e.overrides().get(144, 1.0), 6)))
    return out


def view(result: ToolResult, name: str) -> dict:
    return next(v for v in result.data["views"] if v["name"] == name)


def read(result: ToolResult, **kwargs) -> ToolResult:
    out = getattr(read_tool, "fn", read_tool)(path=str(artifact(result, "drawing.dxf")), max_items=500, **kwargs)
    assert out.ok, out.error
    return out


@pytest.fixture(scope="module")
def probe(models):
    return run(models["probe"])


@pytest.fixture(scope="module")
def plate(models):
    return run(models["plate"])


# --------------------------------------------------------------------------- the callout and the ordinates of the probe part


def test_the_probe_hole_is_called_out_as_diameter_10_thru_in_the_top_view(probe):
    assert probe.ok, probe.error
    assert f"{DIA}10 THRU" in texts(probe)
    (callout,) = probe.data["callouts"]
    assert (callout["text"], callout["view"], callout["count"], callout["diameter_mm"], callout["thru"], callout["depth_mm"]) == (f"{DIA}10 THRU", "top", 1, 10.0, True, None)
    assert callout["hole_ids"] == ["H1"] and callout["from_features_file"] is False and callout["fit"] == ""
    assert abs(callout["axis"][2]) == 1.0
    assert callout["entry_points_mm"][0][:2] == pytest.approx([25.0, 25.0])
    assert probe.values["callout_count"].quantity == 1


def test_the_probe_hole_has_ordinates_0_and_25_in_x_and_in_y(probe):
    assert sorted(ordinates(probe)) == [("x", 0.0), ("x", 25.0), ("y", 0.0), ("y", 25.0)]
    assert [(o["axis"], o["value_mm"], o["text"]) for o in probe.data["ordinates"]] == [("x", 0.0, "0"), ("x", 25.0, "25"), ("y", 0.0, "0"), ("y", 25.0, "25")]
    assert {o["view"] for o in probe.data["ordinates"]} == {"top"}


def test_the_callout_text_and_the_leader_sit_on_the_dim_layer_the_leader_starts_on_the_hole_edge(probe):
    f = dxf_of(probe)
    (callout_text,) = [e for e in f.of("TEXT") if e.text == f"{DIA}10 THRU"]
    assert callout_text.layer == "DIM"
    (leader,) = f.of("LEADER")
    assert leader.layer == "DIM"
    vertices = list(zip(leader.numbers(10), leader.numbers(20)))
    assert len(vertices) == 2
    top = view(probe, "top")
    ox, oy = top["origin_mm"]
    centre = (ox + 25.0, oy + 25.0)  # scale 1:1
    assert math.dist(vertices[0], centre) == pytest.approx(5.0, abs=1e-4)  # starts on the circle R5
    tx, ty = callout_text.point(10, 20)
    assert vertices[1][0] == pytest.approx(tx - 1.0, abs=1e-4) and ty + 1.75 == pytest.approx(vertices[1][1], abs=1e-4)  # ends at the text, at its mid height


def test_the_callout_is_to_the_right_of_the_view_and_touches_no_other_view(probe):
    f = dxf_of(probe)
    top = view(probe, "top")
    right_edge = top["origin_mm"][0] + top["extent_mm"][0]
    (callout_text,) = [e for e in f.of("TEXT") if e.text == f"{DIA}10 THRU"]
    x0 = callout_text.point(10, 20)[0]
    assert x0 == pytest.approx(right_edge + 6.0, abs=1e-4)
    x1 = x0 + S.text_em(callout_text.text) * 3.5
    for other in ("front", "right"):
        ox, oy = view(probe, other)["origin_mm"]
        w, h = view(probe, other)["extent_mm"]
        ty = callout_text.point(10, 20)[1]
        assert not (x0 < ox + w and ox < x1 and ty < oy + h and oy < ty + 3.5), other
    assert not any("runs into" in w or "overlap" in w for w in probe.warnings)


def test_the_overall_dimensions_move_out_one_row_the_ordinates_are_nearest_the_view(probe):
    top = view(probe, "top")
    ox, oy = top["origin_mm"]
    f = dxf_of(probe)
    ordinate_ends = [e.point(14, 24) for e in f.of("DIMENSION") if e.dim_kind == 6]
    assert {round(p[1] - oy, 4) for p in ordinate_ends if abs(p[0] - ox - 25) < 1e-6 or abs(p[0] - ox) < 1e-6} >= {-8.0}  # x ordinates end 8 mm below the view
    assert {round(p[0] - ox, 4) for p in ordinate_ends if abs(p[1] - oy - 25) < 1e-6 or abs(p[1] - oy) < 1e-6} >= {-8.0}  # y ordinates end 8 mm left of it
    (y_dim,) = [d for d in probe.data["dimensions"] if d["source"] == "bbox:y"]
    assert y_dim["view"] == "top"
    base_x = [e.point(10, 20)[0] for e in f.of("DIMENSION") if e.dim_kind == 0 and e.number(50, 0.0) == 90.0]
    assert any(abs(x - (ox - 16.0)) < 1e-4 for x in base_x)  # the Y dimension of the top view is in the second column to the left of it


def test_a_hole_on_the_far_side_of_a_view_list_is_found_whatever_the_view_order(models):
    for views in ("top,front,right", "front,right,top", "right,top"):
        r = run(models["probe"], views=views)
        assert r.ok and [c["view"] for c in r.data["callouts"]] == ["top"], views


def test_the_callouts_are_in_the_summary_of_drawing_read(probe):
    out = read(probe)
    assert "THRU" in " ".join(t["text"] for t in out.data["texts"]["items"])


# --------------------------------------------------------------------------- the plate: groups and several ordinates


def test_four_equal_holes_are_one_callout_and_the_blind_hole_gives_its_depth(plate):
    assert plate.ok, plate.error
    callouts = {c["text"]: c for c in plate.data["callouts"]}
    assert set(callouts) == {f"4{TIMES} {DIA}6.6 THRU", f"{DIA}5 DEEP 8"}
    four = callouts[f"4{TIMES} {DIA}6.6 THRU"]
    assert four["count"] == 4 and len(four["hole_ids"]) == 4 and four["diameter_mm"] == 6.6 and four["thru"] is True
    assert sorted(tuple(p[:2]) for p in four["entry_points_mm"]) == [(10.0, 10.0), (10.0, 40.0), (90.0, 10.0), (90.0, 40.0)]
    blind = callouts[f"{DIA}5 DEEP 8"]
    assert blind["count"] == 1 and blind["depth_mm"] == pytest.approx(8.0, abs=1e-6) and blind["thru"] is False
    assert {c["view"] for c in callouts.values()} == {"top"}
    assert sorted(t for t in texts(plate) if "THRU" in t or "DEEP" in t) == [f"4{TIMES} {DIA}6.6 THRU", f"{DIA}5 DEEP 8"]
    leaders = dxf_of(plate).of("LEADER")
    assert len(leaders) == 2  # one leader per group, not per hole
    ox, oy = view(plate, "top")["origin_mm"]
    four = next(e for e in leaders if abs(e.numbers(20)[0] - (oy + 10.0)) < 1e-6)  # the leader of the 4x group runs along y = 10
    assert math.dist((four.numbers(10)[0], four.numbers(20)[0]), (ox + 90.0, oy + 10.0)) == pytest.approx(3.3, abs=1e-4)  # it starts on the hole nearest the text, so it crosses no other hole


def test_the_plate_has_one_ordinate_per_distinct_coordinate(plate):
    assert sorted(ordinates(plate)) == sorted([("x", v) for v in (0.0, 10.0, 50.0, 90.0)] + [("y", v) for v in (0.0, 10.0, 25.0, 40.0)])
    assert plate.values["dimension_count"].quantity == 3 + 8
    assert not any("overlap" in w for w in plate.warnings), plate.warnings


def test_the_callout_texts_do_not_overlap_each_other(plate):
    f = dxf_of(plate)
    boxes = []
    for e in f.of("TEXT", "DIM"):
        if "THRU" in e.text or "DEEP" in e.text:
            x, y = e.point(10, 20)
            boxes.append((x, y, x + S.text_em(e.text) * 3.5, y + 3.5))
    assert len(boxes) == 2
    (a, b) = boxes
    assert a[3] <= b[1] + 1e-6 or b[3] <= a[1] + 1e-6


# --------------------------------------------------------------------------- scale, DIMLFAC and the reader


def test_at_one_to_two_the_ordinates_still_read_true_size(models):
    r = run(models["probe"], scale="1:2")
    assert r.ok, r.error
    assert sorted(ordinates(r)) == [("x", 0.0), ("x", 25.0), ("y", 0.0), ("y", 25.0)]
    top = view(r, "top")
    assert top["extent_mm"] == pytest.approx([50.0, 25.0], abs=1e-3)  # drawn half size
    f = dxf_of(r)
    drawn = sorted(abs(e.point(13, 23)[0] - e.point(10, 20)[0]) for e in f.of("DIMENSION") if e.dim_kind == 6 and int(e.get(70)) & 64)
    assert drawn == pytest.approx([0.0, 12.5], abs=1e-4)


def test_drawing_read_reads_the_callout_and_the_ordinates_of_our_own_dxf(probe):
    out = read(probe)
    items = [t["text"] for t in out.data["texts"]["items"]]
    assert f"{DIA}10 THRU" in items
    dims = out.data["dimensions"]["items"]
    ordinate_dims = [d for d in dims if d["kind"] == "ordinate"]
    assert sorted(d["value_mm"] for d in ordinate_dims) == pytest.approx([0.0, 0.0, 25.0, 25.0])
    assert sorted(d["text"] for d in ordinate_dims) == ["0", "0", "25", "25"]
    assert sorted(d["value_mm"] for d in dims if d["kind"] == "linear") == pytest.approx([20.0, 50.0, 100.0])


def test_drawing_read_reads_the_plate_callouts_and_ordinates_at_one_to_two(models):
    r = run(models["plate"], scale="1:2")
    out = read(r)
    items = [t["text"] for t in out.data["texts"]["items"]]
    assert f"4{TIMES} {DIA}6.6 THRU" in items and f"{DIA}5 DEEP 8" in items
    values = sorted(d["value_mm"] for d in out.data["dimensions"]["items"] if d["kind"] == "ordinate")
    assert values == pytest.approx(sorted([0.0, 10.0, 50.0, 90.0, 0.0, 10.0, 25.0, 40.0]))


# --------------------------------------------------------------------------- modes and models without holes


def test_overall_mode_draws_no_callouts_and_no_ordinates(models):
    r = run(models["probe"], dimensions="overall")
    assert r.ok and r.data["callouts"] == [] and r.data["ordinates"] == [] and r.values["callout_count"].quantity == 0
    assert dxf_of(r).of("LEADER") == [] and not any(e.dim_kind == 6 for e in dxf_of(r).of("DIMENSION"))
    assert r.values["dimension_count"].quantity == 3


def test_none_mode_draws_nothing(models):
    r = run(models["probe"], dimensions="none")
    assert r.ok and r.data["callouts"] == [] and r.values["dimension_count"].quantity == 0 and dxf_of(r).of("LEADER") == []


def test_a_block_without_holes_has_no_callouts_and_no_warning_about_them(models):
    r = run(models["plain"])
    assert r.ok and r.data["callouts"] == [] and r.data["ordinates"] == [] and r.values["dimension_count"].quantity == 3
    assert not any("hole" in w.lower() for w in r.warnings if not w.startswith("Overall dimensions come from"))  # the standing warning names holes in general


def test_without_a_view_that_looks_along_the_hole_axis_the_callout_is_left_out_with_a_hint(models):
    r = run(models["probe"], views="front,right")
    assert r.ok and r.data["callouts"] == []
    assert any("top view" in w and "H1" in w for w in r.warnings)


def test_a_surface_only_model_has_no_holes_to_call_out_and_says_so(models):
    r = run(models["faces"])
    assert r.ok and r.data["callouts"] == []
    assert any("No holes can be found" in w for w in r.warnings)


# --------------------------------------------------------------------------- features_path and its hash


def test_a_matching_features_file_adds_the_fit_to_the_callout(made_part):
    r = run(made_part["step"], features_path=str(made_part["features"]))
    assert r.ok, r.error
    assert f"{DIA}10 H7 THRU" in texts(r) and f"{DIA}10 THRU" not in texts(r)
    (callout,) = r.data["callouts"]
    assert callout["fit"] == "H7" and callout["from_features_file"] is True and callout["text"] == f"{DIA}10 H7 THRU"
    features = r.data["features"]
    step_hash = hashlib.sha256(made_part["step"].read_bytes()).hexdigest()
    assert (features["status"], features["given"], features["declared_holes"], features["matched_holes"]) == ("bound", True, 1, 1)
    assert features["model_sha256"] == features["step_sha256_in_file"] == step_hash
    assert [Path(f.path).name for f in r.provenance.input_files] == ["bracket.step", "bracket.features.json"]
    assert not any("features_path" in w for w in r.warnings)


def test_the_same_model_without_the_features_file_prints_no_fit(made_part):
    r = run(made_part["step"])
    assert f"{DIA}10 THRU" in texts(r) and not any("H7" in t for t in texts(r))
    assert r.data["features"] == {"given": False, "status": "not given"}


def test_a_features_file_of_another_model_warns_and_prints_no_fit(made_part, models, tmp_path):
    """The file is bound to the example part's STEP; the probe part is another file, so its sha256 differs."""
    r = run(models["probe"], features_path=str(made_part["features"]))
    assert r.ok, r.error
    assert f"{DIA}10 THRU" in texts(r) and not any("H7" in t for t in texts(r))
    assert r.data["features"]["status"] == "hash_mismatch" and r.data["features"]["matched_holes"] == 0
    (warning,) = [w for w in r.warnings if w.startswith("features_path")]
    assert "no fit or thread is printed" in warning and r.data["features"]["step_sha256_in_file"][:12] in warning
    assert r.data["callouts"][0]["from_features_file"] is False


def test_a_changed_model_file_breaks_the_binding(made_part, tmp_path):
    changed = tmp_path / "bracket.step"
    shutil.copy(made_part["step"], changed)
    changed.write_bytes(changed.read_bytes() + b"\n")  # one byte more: the geometry is the same, the file is not
    r = run(changed, features_path=str(made_part["features"]))
    assert r.data["features"]["status"] == "hash_mismatch" and not any("H7" in t for t in texts(r))


def test_a_features_file_that_names_a_hole_the_model_does_not_have(made_part, tmp_path):
    document = json.loads(made_part["features"].read_text(encoding="utf-8"))
    document["holes"][0]["diameter_mm"] = 12.0  # the model's hole is 10
    document["holes"].append({**document["holes"][0], "diameter_mm": 10.0, "entry_mm": [75.0, 25.0, 20.0], "fit": "H8", "feature": "ghost"})
    path = tmp_path / "edited.features.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    r = run(made_part["step"], features_path=str(path))
    assert r.ok and f"{DIA}10 THRU" in texts(r) and not any("H7" in t or "H8" in t for t in texts(r))
    assert r.data["features"]["status"] == "bound" and r.data["features"]["matched_holes"] == 0
    assert any("2 of the 2 declared holes are not in the model" in w for w in r.warnings)


def test_a_features_file_is_not_read_when_no_callouts_are_drawn(made_part):
    r = run(made_part["step"], features_path=str(made_part["features"]), dimensions="overall")
    assert r.ok and any("features_path was not read" in w for w in r.warnings)
    assert [Path(f.path).name for f in r.provenance.input_files] == ["bracket.step"]


@pytest.mark.parametrize(
    ("content", "pointer"),
    [("not json", "/"), ('{"schema": "other"}', "/schema"), (json.dumps({"schema": "eng-toolkit/features/1", "step_sha256": "zz", "holes": []}), "/step_sha256")],
)
def test_a_bad_features_file_is_input_invalid_with_its_pointer(models, tmp_path, content, pointer):
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    r = run(models["probe"], features_path=str(path))
    assert r.ok is False and r.error.family == "input.invalid"
    assert r.error.details["param"] == "features_path" and r.error.details["pointer"] == pointer


def test_a_missing_features_file_and_a_wrong_extension(models, tmp_path):
    missing = run(models["probe"], features_path=str(tmp_path / "nope.json"))
    assert missing.ok is False and missing.error.family == "input.file_not_found" and missing.error.details["param"] == "features_path"
    wrong = tmp_path / "features.txt"
    wrong.write_text("{}")
    r = run(models["probe"], features_path=str(wrong))
    assert r.ok is False and r.error.family == "input.unsupported_format"


def test_text_from_the_features_file_cannot_smuggle_a_second_line_into_the_drawing(made_part, tmp_path):
    document = json.loads(made_part["features"].read_text(encoding="utf-8"))
    document["holes"][0]["fit"] = "H7\n0\nTEXT"
    path = tmp_path / "evil.features.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    r = run(made_part["step"], features_path=str(path))
    assert r.ok is False and r.error.family == "input.invalid" and r.error.details["pointer"] == "/holes/0/fit"
