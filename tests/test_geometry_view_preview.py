"""geometry_view's PNG preview and its named highlight groups, through the tool and through the gateway.

What is checked, and against what:

* The PNG is a valid file (tests/png_reader.py: struct and zlib only) of the size ``data.preview`` states, written next to the GLB,
  the same bytes on a second run, and its presence changes nothing in the GLB (same sha256 with ``preview="none"``).
* Colours: every legend group with faces shows its exact GLB colour in a legend swatch and, in the picture, pixels that are that
  colour times one Lambert factor.  For the DFM colouring this is the red of ``dfm_error`` and the grey of ``neutral``.
* Axes: the small front, top and right views of the 100 x 50 x 20 mm probe part are 5 : 1, 2 : 1 and 2.5 : 1 rectangles (front
  x by z, top x by y, right y by z): Z is up in the front and right views, Y up in the top view.
* Named groups: 'bends: F5,F12; holes: F3-F4' makes one primitive and one colour per group in the GLB and the PNG.
* A failure to draw the picture (memory, time) is a warning: the GLB is still written.  Pillow is not needed, and a process in
  which Pillow cannot be imported still writes the PNG.
Engine tests skip without OCP.
"""

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from fastmcp import Client
from png_reader import read_png
from view_readers import groups_of_faces, read_view_glb

from eng_toolkit import preview as P
from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.geometry import view as viewer
from eng_toolkit.domains.geometry.tools import view
from eng_toolkit.errors import ToolFailure
from eng_toolkit.gateway.server import gateway

NEED_OCP = "OCP is not installed; run: uv sync --extra cad"
ISO_PANEL = (slice(84 + 24, 84 + 500 - 4), slice(P.MARGIN + 2, P.MARGIN + P.LEFT_W - 2))  # inside the isometric panel of the four-view sheet, below its caption
LEGEND_COLUMN = slice(800, 1200)


@pytest.fixture(scope="module")
def ocp():
    return pytest.importorskip("OCP", reason=NEED_OCP)


@pytest.fixture()
def cad(ocp):
    import cad_fixtures

    return cad_fixtures


@pytest.fixture()
def fx(ocp):
    import dfm_fixtures

    return dfm_fixtures


def run(path, **kwargs) -> ToolResult:
    fn = getattr(view, "fn", view)
    result = fn(model_path=str(path), **kwargs)
    assert isinstance(result, ToolResult) and result.review_status == "unreviewed"
    return result


def refused(result: ToolResult, family: str, param: str) -> ToolResult:
    assert result.ok is False, "expected a refusal"
    assert result.error.family == family, result.error
    assert result.error.details["param"] == param, result.error
    return result


def artifact(result: ToolResult, name: str) -> Path:
    (found,) = [a for a in result.artifacts if Path(a.path).name == name]
    assert hashlib.sha256(Path(found.path).read_bytes()).hexdigest() == found.sha256
    return Path(found.path)


def names(result: ToolResult) -> list[str]:
    return [Path(a.path).name for a in result.artifacts if Path(a.path).name != "inputs.json"]


def hex_rgb(colour: str) -> tuple[int, int, int]:
    return (int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16))


def shaded_matches(region: np.ndarray, base) -> int:
    """Pixels of ``region`` equal to ``base`` times one factor in [0.5, 1] (a Lambert shade of that colour)."""
    base = np.asarray(base, float)
    pixels = region.reshape(-1, 3).astype(float)
    factor = pixels @ base / (base @ base)
    off = np.abs(pixels - factor[:, None] * base).max(axis=1)
    return int(np.sum((off <= 3.0) & (factor >= 0.5) & (factor <= 1.001)))


def exact_matches(region: np.ndarray, colour) -> int:
    return int(np.all(region == np.asarray(colour, dtype=np.uint8), axis=2).sum())


def picture(result: ToolResult, name: str = "view.png") -> np.ndarray:
    data = artifact(result, name).read_bytes()
    width, height, image = read_png(data)
    assert (width, height) == (result.data["preview"]["width"], result.data["preview"]["height"])
    return image


# --------------------------------------------------------------------------- the file


def test_the_default_is_a_png_next_to_the_glb_that_a_second_run_reproduces_byte_for_byte(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    r = run(source, color_by="face_kind")
    assert r.ok, r.error
    assert names(r) == ["view.glb", "view.png", "view.json"]
    assert {Path(a.path).name: a.format for a in r.artifacts if Path(a.path).name != "inputs.json"} == {"view.glb": "glb", "view.png": "png", "view.json": "json"}
    png = artifact(r, "view.png").read_bytes()
    width, height, image = read_png(png)
    assert width == 1200 and 700 < height < 900 and image.shape == (height, width, 3)
    p = r.data["preview"]
    assert p["file"] == "view.png" and p["sha256"] == hashlib.sha256(png).hexdigest() and p["format"] == "PNG, 8-bit RGB"
    assert p["views"] == ["iso", "front", "top", "right"] and p["edges_drawn"] is True
    assert p["model_size_mm"] == {"x": 100.0, "y": 50.0, "z": 20.0}
    assert json.loads(artifact(r, "view.json").read_text(encoding="utf-8"))["preview"] == "view.png"
    again = run(source, color_by="face_kind")
    assert artifact(again, "view.png").read_bytes() == png
    assert artifact(again, "view.glb").read_bytes() == artifact(r, "view.glb").read_bytes()


def test_preview_none_writes_no_png_and_the_glb_is_the_same_with_or_without_the_picture(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    with_png = run(source, color_by="face_kind")
    without = run(source, color_by="face_kind", preview="none")
    assert without.ok and names(without) == ["view.glb", "view.json"]
    assert without.data["preview"] is None and json.loads(artifact(without, "view.json").read_text(encoding="utf-8"))["preview"] is None
    assert artifact(without, "view.glb").read_bytes() == artifact(with_png, "view.glb").read_bytes()
    assert without.value.quantity == with_png.value.quantity and without.values.keys() == with_png.values.keys()


def test_iso_is_one_view_and_png_is_four(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    four, one = run(source), run(source, preview="iso")
    assert four.data["preview"]["views"] == ["iso", "front", "top", "right"] and one.data["preview"]["views"] == ["iso"]
    assert picture(one).shape[0] > picture(four).shape[0] - 200 and picture(one).shape[1] == 1200
    assert one.data["supported_preview"] == ["png", "iso", "none"]


@pytest.mark.parametrize("text", ["gif", "PNGS", "", " none x", "x" * 100])
def test_an_unknown_preview_is_refused_with_its_pointer(cad, tmp_path, text):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    r = refused(run(source, preview=text), "input.invalid", "/preview")
    assert "x" * 70 not in r.error.message


def test_preview_is_case_and_blank_insensitive(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    r = run(source, preview=" None ")
    assert r.ok and r.data["preview"] is None


@pytest.mark.asyncio
async def test_the_gateway_returns_the_png_as_an_artifact(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    async with Client(gateway) as client:
        raw = await client.call_tool("geometry_view", {"model_path": str(source), "name": "probe"})
    data = raw.structured_content if getattr(raw, "structured_content", None) else json.loads(raw.content[0].text)
    r = ToolResult.model_validate(data)
    assert r.ok, r.error
    assert names(r) == ["probe.glb", "probe.png", "view.json"], names(r)
    read_png(artifact(r, "probe.png").read_bytes())


# --------------------------------------------------------------------------- colours and axes


def test_every_legend_group_shows_its_glb_colour_in_the_swatch_and_shaded_in_the_picture(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    r = run(source, color_by="face_kind")
    image = picture(r)
    legend = {row["group"]: row for row in r.data["legend"]}
    seen = {row["group"]: row for row in r.data["preview"]["groups"]}
    assert set(seen) == {"plane", "cylinder"}, "only groups that have faces are in the picture's legend"
    for group, row in seen.items():
        colour = hex_rgb(legend[group]["color"])
        assert row["color"] == legend[group]["color"] and row["faces"] == legend[group]["faces"]
        assert exact_matches(image[:, LEGEND_COLUMN], colour) >= 26 * 16 * 0.95, "the legend swatch is the exact GLB colour"
        assert shaded_matches(image[ISO_PANEL], colour) >= (400 if group == "plane" else 30), group
        assert row["share_of_model_in_isometric_view"] > 0.0
    assert seen["plane"]["share_of_model_in_isometric_view"] + seen["cylinder"]["share_of_model_in_isometric_view"] == pytest.approx(1.0, abs=0.01)
    # the edge entry of the legend is the dark line colour
    assert exact_matches(image[:, LEGEND_COLUMN], hex_rgb(viewer.EDGE_COLOR)) >= 28 * 3 * 0.9


def test_dfm_colours_are_the_glb_colours_and_neutral_faces_stay_grey(fx, cad, tmp_path):
    source = cad.write_step(fx.wall_block(1.0), tmp_path / "wall.step")
    r = run(source, color_by="dfm")
    assert r.ok, r.error
    image = picture(r)
    groups = {row["group"]: row for row in r.data["preview"]["groups"]}
    assert "dfm_error" in groups and "neutral" in groups
    for group, row in groups.items():
        colour = hex_rgb(row["color"])
        assert exact_matches(image[:, LEGEND_COLUMN], colour) >= 26 * 16 * 0.95, group
        assert shaded_matches(image[ISO_PANEL], colour) > 100, group
    glb = groups_of_faces(read_view_glb(artifact(r, "view.glb")))
    assert {g for g in glb.values()} == set(groups), "the PNG legend lists the groups of the GLB, no more and no less"
    # the red of the GLB's dfm_error material (sRGB #D62728) is what the picture uses
    assert groups["dfm_error"]["color"] == "#D62728"


def test_the_small_views_have_z_up_in_front_and_right_and_y_up_in_the_top_view(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    r = run(source, color_by="none", show_edges=False)
    image = picture(r)
    top = 84 + 500 + 12
    panel_w, gap = (P.LEFT_W - 20) // 3, 10
    boxes = {}
    for k, name in enumerate(("front", "top", "right")):
        x0 = 16 + k * (panel_w + gap)
        region = image[top + 22 : top + 170 - 3, x0 + 3 : x0 + panel_w - 3]
        ink = np.any(region != 255, axis=2)
        rows, cols = np.flatnonzero(ink.any(axis=1)), np.flatnonzero(ink.any(axis=0))
        boxes[name] = (cols.max() - cols.min() + 1, rows.max() - rows.min() + 1)
    # the probe part is 100 x 50 x 20 mm: front x by z, top x by y, right y by z
    assert boxes["front"][0] / boxes["front"][1] == pytest.approx(100 / 20, rel=0.06)
    assert boxes["top"][0] / boxes["top"][1] == pytest.approx(100 / 50, rel=0.06)
    assert boxes["right"][0] / boxes["right"][1] == pytest.approx(50 / 20, rel=0.06)


def test_show_edges_false_draws_no_edges_in_the_picture(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    with_edges = run(source, color_by="none")
    without = run(source, color_by="none", show_edges=False)
    assert with_edges.data["preview"]["edges_drawn"] is True and without.data["preview"]["edges_drawn"] is False
    dark = lambda r: int(np.sum(np.all(picture(r)[ISO_PANEL] < 80, axis=2)))  # noqa: E731
    assert dark(with_edges) > 300 and dark(without) == 0


def test_the_preview_does_not_change_what_the_tool_reports_about_the_glb(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    a, b = run(source, preview="none"), run(source)
    strip = lambda d: {k: v for k, v in d.items() if k not in ("preview", "supported_preview", "job_id", "job_dir")}  # noqa: E731
    assert strip(a.data) == strip(b.data)
    assert [w for w in b.warnings if w not in a.warnings] == []


# --------------------------------------------------------------------------- named highlight groups


def test_a_single_unnamed_highlight_is_unchanged(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    r = run(source, color_by="highlight", highlight="F1")
    assert r.ok and [g["group"] for g in r.data["groups"]] == ["highlight", "neutral"]
    assert r.data["highlight_groups"] == [{"name": "highlight", "color": "#E6007E", "faces": 1, "face_ids": ["F1"]}]
    assert r.data["highlighted_faces"] == ["F1"]


def test_named_groups_get_one_colour_each_in_the_glb_the_json_and_the_picture(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    kinds = {row["id"]: row["kind"] for row in json.loads(artifact(run(source, color_by="face_kind"), "view.json").read_text(encoding="utf-8"))["faces"]}
    cylinders = [k for k, v in kinds.items() if v == "cylinder"]
    planes = [k for k, v in kinds.items() if v == "plane"]
    bore, fillets = cylinders[:1], cylinders[1:]
    text = f"bore: {','.join(bore)}; fillets: {' '.join(fillets)}\nbottom and top : {planes[0]}, {planes[1]}"
    r = run(source, color_by="highlight", highlight=text)
    assert r.ok, r.error
    names = ["bore", "fillets", "bottom and top"]
    assert [g["name"] for g in r.data["highlight_groups"]] == names
    colours = [g["color"] for g in r.data["highlight_groups"]]
    assert colours == list(viewer.GROUP_COLOURS[:3]) and len(set(colours)) == 3
    assert [g["faces"] for g in r.data["highlight_groups"]] == [1, 4, 2]
    assert r.values["highlighted_face_count"].quantity == 7 and len(r.data["highlighted_faces"]) == 7
    parsed = read_view_glb(artifact(r, "view.glb"))
    by_face = groups_of_faces(parsed)
    for face in bore:
        assert by_face[face] == "bore"
    for face in fillets:
        assert by_face[face] == "fillets"
    assert by_face[planes[0]] == by_face[planes[1]] == "bottom and top"
    assert all(by_face[f] == "neutral" for f in planes[2:])
    materials = {p["extras"]["group"]: p["extras"]["color"] for p in parsed["primitives"] if p["mode"] == 4}
    assert materials == {"bore": colours[0], "fillets": colours[1], "bottom and top": colours[2], "neutral": "#B4B8BE"}
    table = json.loads(artifact(r, "view.json").read_text(encoding="utf-8"))
    assert {row["id"]: row["group"] for row in table["faces"]} == by_face
    image = picture(r)
    for colour in (*colours, "#B4B8BE"):
        assert exact_matches(image[:, LEGEND_COLUMN], hex_rgb(colour)) >= 26 * 16 * 0.95, colour
    for row in r.data["preview"]["groups"]:
        assert row["share_of_model_in_isometric_view"] >= 0.0
    assert {row["group"] for row in r.data["preview"]["groups"]} == set(names) | {"neutral"}


def test_a_range_names_consecutive_faces(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    r = run(source, color_by="highlight", highlight="first: F1-F3; rest: F4-8, F9 F10-F11")
    assert r.ok, r.error
    assert [(g["name"], g["face_ids"]) for g in r.data["highlight_groups"]] == [
        ("first", ["F1", "F2", "F3"]),
        ("rest", [f"F{i}" for i in range(4, 12)]),
    ]
    assert not any(g["group"] == "neutral" for g in r.data["groups"]), "every face is in a group"


def test_group_text_errors_are_refused_at_highlight(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    cases = {
        "bends: F1; F2": "input.invalid",  # F2 has no name
        "a: F1; a: F2": "input.invalid",  # a name twice
        "A: F1; a: F2": "input.invalid",  # ... in any case
        "neutral: F1": "input.invalid",  # reserved
        "Edges: F1": "input.invalid",  # reserved
        "a/b: F1": "input.invalid",  # a character that is not a name's
        ": F1": "input.invalid",  # no name
        "a: F1; b: F1": "input.invalid",  # one face, two colours
        "a: F3-F1": "input.invalid",  # backwards
        "a: ": "input.missing",  # no faces
        "a: F1-F30000": "input.too_large",
        "; ;": "input.missing",
        "a: F1; b: F2; c: F3; d: F4; e: F5; f: F6; g: F7; h: F8; i: F9": "input.too_large",  # nine colours
        "a: F99": "geometry.unknown_entity",
    }
    for text, family in cases.items():
        refused(run(source, color_by="highlight", highlight=text), family, "/highlight")
    refused(run(source, color_by="face_kind", highlight="a: F1"), "input.invalid", "/highlight")  # only with color_by highlight


def test_the_group_parser_alone():
    assert viewer.parse_highlight_groups("F7, f3 F3;F1") == [("highlight", [1, 3, 7])], "no colon: the one unnamed group of before"
    assert viewer.parse_highlight("F2-F4, F9") == [2, 3, 4, 9] and viewer.parse_highlight("F2-4") == [2, 3, 4]
    assert viewer.parse_highlight_groups("bends: F5,F12 | holes: F3-F4;\nribs: F14-F19") == [
        ("bends", [5, 12]),
        ("holes", [3, 4]),
        ("ribs", [14, 15, 16, 17, 18, 19]),
    ]
    assert viewer.parse_highlight_groups("  Two words - and_more : F1 ")[0][0] == "Two words - and_more"
    palette = viewer.palette_for("highlight", viewer.parse_highlight_groups("a: F1; b: F2"))
    assert [(g, c) for g, c, _ in palette] == [("a", viewer.GROUP_COLOURS[0]), ("b", viewer.GROUP_COLOURS[1]), ("neutral", "#B4B8BE")]
    assert viewer.palette_for("highlight", [("highlight", [1])]) == viewer.PALETTES["highlight"]
    colours = list(viewer.GROUP_COLOURS)
    assert len(colours) == viewer.MAX_HIGHLIGHT_GROUPS == len(set(colours)) and "#B4B8BE" not in colours and viewer.EDGE_COLOR not in colours
    for text in ("F1-F2-F3", "F1-", "F-2", "F2-F1"):
        with pytest.raises(ToolFailure) as caught:
            viewer.parse_highlight(text)
        assert caught.value.details["param"] == "/highlight", text


# --------------------------------------------------------------------------- what the picture says about hidden faces and failures


def test_a_group_that_no_view_can_see_is_reported(cad, tmp_path):
    source = cad.write_step(cad.box(), tmp_path / "box.step")
    measured = run(source, color_by="none", preview="none")
    assert measured.ok
    table = json.loads(artifact(measured, "view.json").read_text(encoding="utf-8"))
    assert len(table["faces"]) == 6
    # the box's six faces by their outward normals: find the face at z = 0 (its bottom) by asking geometry_measure
    from eng_toolkit.domains.geometry.tools import measure

    inventory = getattr(measure, "fn", measure)(model_path=str(source), inventory_limit=50)
    bottom = [f"F{f['id']}" for f in inventory.data["faces"] if f["kind"] == "plane" and f["normal"][2] < -0.5]
    assert len(bottom) == 1
    r = run(source, color_by="highlight", highlight=f"bottom: {bottom[0]}")
    assert r.ok, r.error
    assert any("'bottom'" in w and "not visible" in w for w in r.warnings), r.warnings
    iso = run(source, color_by="highlight", highlight=f"bottom: {bottom[0]}", preview="iso")
    assert any("'bottom'" in w and "not visible" in w for w in iso.warnings)


def test_a_picture_that_cannot_be_drawn_is_a_warning_and_the_glb_is_kept(cad, tmp_path, monkeypatch):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")

    def boom(*args, **kwargs):
        raise MemoryError("out of memory in the rasteriser")

    monkeypatch.setattr(viewer.pic, "render_sheet", boom)
    r = run(source, color_by="face_kind")
    assert r.ok, r.error
    assert names(r) == ["view.glb", "view.json"]
    assert any("PNG preview was not drawn" in w and "MemoryError" in w and "GLB is complete" in w for w in r.warnings)
    assert r.data["preview"]["file"] is None and "MemoryError" in r.data["preview"]["reason"]
    read_view_glb(artifact(r, "view.glb"))


def test_a_time_budget_that_runs_out_during_the_picture_keeps_the_glb(cad, tmp_path, monkeypatch):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")

    def late(mesh, spec, *, check=None):
        raise ToolFailure("geometry.time_budget_exceeded", "time budget exceeded in preview: used 2 s, limit 1 s", {"param": "/max_seconds"})

    monkeypatch.setattr(viewer.pic, "render_sheet", late)
    r = run(source)
    assert r.ok, r.error
    assert any("PNG preview was not drawn" in w and "time budget" in w for w in r.warnings)
    assert names(r) == ["view.glb", "view.json"] and r.data["preview"]["reason"] == "time budget"


def test_the_pictures_own_time_limit_drops_only_the_picture(cad, tmp_path, monkeypatch):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    monkeypatch.setattr(viewer, "PREVIEW_SECONDS", -1.0)  # every check of the render finds the limit passed
    r = run(source)
    assert r.ok, r.error
    assert any("PNG preview was not drawn" in w and "TimeoutError" in w for w in r.warnings), r.warnings
    assert names(r) == ["view.glb", "view.json"]


def test_the_picture_is_written_where_pillow_cannot_be_imported(cad, tmp_path, monkeypatch):
    for name in ("PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont", "matplotlib"):
        monkeypatch.setitem(sys.modules, name, None)  # an import of any of them raises ImportError now
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    r = run(source, color_by="face_kind")
    assert r.ok, r.error
    assert not any("PNG" in w for w in r.warnings)
    read_png(artifact(r, "view.png").read_bytes())
