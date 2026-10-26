"""fem_view's PNG preview: the synthetic one-tetrahedron job (no program needed) and, where Gmsh, CalculiX and OCP are installed, a
real cantilever.

Checked against:

* the independent PNG reader (tests/png_reader.py: struct and zlib only) and the independent colour-map reader of
  tests/fem_view_reader.py (cubehelix written out again from Green's formula): the colour bar of the picture, read pixel by pixel,
  is the map the GLB uses, from the low end at its foot to the high end at its top;
* the GLB: with or without the picture it is the same bytes;
* hand numbers: the synthetic tetrahedron is 10 x 10 x 10 mm and its von Mises field x + y + z runs 0 to 10 MPa; the clamped
  cantilever 100 x 10 x 10 mm under 1 kN has a tip deflection near 1.90 mm (beam theory 1.9048), so field 'uz' has its minimum
  there and its range is symmetric about zero.
"""

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from png_reader import read_png

from eng_toolkit import preview as P
from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.fem import view as viewer
from tests.fem_view_reader import sequential_table
from tests.test_fem_view import HAVE_OCP, HAVE_PROGRAMS, fake_job, refused, run, sign, tet_nodes, write_csv  # noqa: F401

BAR_X = P.RIGHT_X + 4
BAR_HEIGHT = 330


@pytest.fixture
def job(tmp_path):
    return fake_job(tmp_path / "jobs" / "20261023T000000Z-00000000-fem_static")


def names(result: ToolResult) -> list[str]:
    return [Path(a.path).name for a in result.artifacts if Path(a.path).name != "inputs.json"]


def artifact(result: ToolResult, name: str) -> Path:
    (found,) = [a for a in result.artifacts if Path(a.path).name == name]
    assert hashlib.sha256(Path(found.path).read_bytes()).hexdigest() == found.sha256
    return Path(found.path)


def picture(result: ToolResult) -> np.ndarray:
    width, height, image = read_png(artifact(result, "fem_view.png").read_bytes())
    assert (width, height) == (result.data["preview"]["width"], result.data["preview"]["height"])
    return image


def colour_bar(image: np.ndarray) -> np.ndarray:
    """The 330 rows of the colour bar, top (high end) first: the one run of that length of non-white pixels in the bar's column."""
    column = image[:, BAR_X + 17]
    inked = np.any(column != 255, axis=1)
    runs, start = [], None
    for y, flag in enumerate(inked):
        if flag and start is None:
            start = y
        if not flag and start is not None:
            runs.append((start, y))
            start = None
    (found,) = [r for r in runs if r[1] - r[0] == BAR_HEIGHT + 2]  # the bar and its one-pixel frame
    return column[found[0] + 1 : found[1] - 1]


# --------------------------------------------------------------------------- the file


def test_the_default_is_a_png_next_to_the_glb_and_the_legend_svg(job):
    r = run(result_dir=str(job))
    assert r.ok, r.error
    assert names(r) == ["fem_view.glb", "fem_view.png", "legend.svg", "view.json"]
    assert {Path(a.path).name: a.format for a in r.artifacts if Path(a.path).name != "inputs.json"}["fem_view.png"] == "png"
    png = artifact(r, "fem_view.png").read_bytes()
    width, height, image = read_png(png)
    assert width == 1200 and 700 < height < 1300 and image.shape == (height, width, 3)
    p = r.data["preview"]
    assert p["file"] == "fem_view.png" and p["sha256"] == hashlib.sha256(png).hexdigest()
    assert p["colour_scale"] == {"low": 0.0, "high": 10.0, "unit": "MPa", "colormap": "sequential"}
    assert p["model_size_mm"] == {"x": 10.0, "y": 10.0, "z": 10.0}
    assert json.loads(artifact(r, "view.json").read_text(encoding="utf-8"))["preview_png"] == "fem_view.png"
    assert r.data["supported_preview"] == ["png", "iso", "none"]
    again = run(result_dir=str(job))
    assert artifact(again, "fem_view.png").read_bytes() == png, "same input, same bytes"


def test_preview_none_writes_no_png_and_the_glb_is_unchanged(job):
    a, b = run(result_dir=str(job), preview="none"), run(result_dir=str(job))
    assert names(a) == ["fem_view.glb", "legend.svg", "view.json"] and a.data["preview"] is None
    assert json.loads(artifact(a, "view.json").read_text(encoding="utf-8"))["preview_png"] is None
    for name in ("fem_view.glb", "legend.svg"):
        assert artifact(a, name).read_bytes() == artifact(b, name).read_bytes(), name
    iso = run(result_dir=str(job), preview="iso")
    assert artifact(iso, "fem_view.png").read_bytes() == artifact(b, "fem_view.png").read_bytes(), "'iso' is the same picture as 'png'"


@pytest.mark.parametrize("text", ["gif", "", "svg", "x" * 200])
def test_an_unknown_preview_is_refused_with_its_pointer(job, text):
    r = refused(run(result_dir=str(job), preview=text), "input.invalid", "/preview")
    assert "x" * 100 not in r.error.message


# --------------------------------------------------------------------------- colours


def test_the_colour_bar_of_the_picture_is_the_glbs_colour_map_read_pixel_by_pixel(job):
    r = run(result_dir=str(job))
    bar = colour_bar(picture(r))
    table = np.array(sequential_table())
    t = (BAR_HEIGHT - 1 - np.arange(BAR_HEIGHT)) / (BAR_HEIGHT - 1)  # the top row is the high end
    x = t * (len(table) - 1)
    i = np.minimum(np.floor(x).astype(int), len(table) - 2)
    expected = table[i] * (1 - (x - i))[:, None] + table[i + 1] * (x - i)[:, None]
    assert np.abs(bar.astype(float) - expected * 255.0).max() <= 1.0  # rounding to 8 bits
    assert bar[-1].tolist() == [round(c * 255) for c in table[0]] and bar[0].tolist() == [round(c * 255) for c in table[-1]]


def test_the_diverging_map_has_light_grey_at_zero_in_the_middle_of_the_bar(job):
    r = run(result_dir=str(job), field="s_xx")  # signed: -10 .. 10 MPa
    assert r.data["preview"]["colour_scale"] == {"low": -10.0, "high": 10.0, "unit": "MPa", "colormap": "diverging"}
    bar = colour_bar(picture(r))
    middle = bar[BAR_HEIGHT // 2].astype(int)
    assert max(middle) - min(middle) <= 3 and middle.min() >= 200, middle  # Moreland's neutral grey
    assert bar[0][0] > bar[0][2] and bar[-1][2] > bar[-1][0], "red at the high end, blue at the low end"


def test_a_uniform_field_is_one_map_colour_times_a_gentle_lambert_factor(job):
    write_csv(job / "results.csv", tet_nodes(), value=lambda i, x, y, z: 5.0)  # the same von Mises stress at every node
    sign(job)
    r = run(result_dir=str(job), range="0 MPa, 10 MPa")  # 5 MPa is the middle of the range: t = 0.5
    image = picture(r)
    region = image[84 + 24 : 84 + 640 - 4, P.MARGIN + 2 : P.MARGIN + P.LEFT_W - 2]
    pixels = region.reshape(-1, 3).astype(float)
    pixels = pixels[np.any(pixels != 255, axis=1)]
    assert len(pixels) > 20_000
    middle = np.array(sequential_table()[32]) * 255.0  # t = 0.5 is the 33rd of the 65 points
    factor = np.clip(pixels @ middle / (middle @ middle), 0.8, 1.0)
    off = np.abs(pixels - factor[:, None] * middle).max(axis=1)
    assert np.mean(off <= 2.5) >= 0.93, "every model pixel but the edge lines is the map's middle colour times a factor between 0.8 and 1"
    assert factor.max() - factor.min() > 0.1, "three faces of the tetrahedron, three shades: the form can be read"


def test_the_range_notes_in_the_picture_follow_the_legend_of_the_glb(job):
    r = run(result_dir=str(job), range="2 MPa, 8 MPa")
    assert r.data["preview"]["colour_scale"]["low"] == 2.0 and r.data["preview"]["colour_scale"]["high"] == 8.0
    bar = colour_bar(picture(r))
    table = np.array(sequential_table())
    assert bar[-1].tolist() == [round(c * 255) for c in table[0]] and bar[0].tolist() == [round(c * 255) for c in table[-1]]
    unit = run(result_dir=str(job), output_unit="psi")
    assert unit.data["preview"]["colour_scale"]["unit"] == "psi" and unit.data["preview"]["colour_scale"]["high"] == pytest.approx(1450.377, rel=1e-6)


# --------------------------------------------------------------------------- markers


def test_a_magnitude_field_is_marked_at_its_maximum_only_and_a_signed_one_at_both_ends(job):
    r = run(result_dir=str(job))
    assert [m["which"] for m in r.data["preview"]["markers"]] == ["max"]
    m = r.data["preview"]["markers"][0]
    assert m["value"] == 10.0 and m["unit"] == "MPa" and 1 <= m["node"] <= 10
    signed = run(result_dir=str(job), field="s_xx")
    assert [m["which"] for m in signed.data["preview"]["markers"]] == ["max", "min"]
    assert [m["value"] for m in signed.data["preview"]["markers"]] == [10.0, 0.0]


def test_a_marker_on_a_face_that_points_away_gets_a_second_view_or_a_warning(job):
    r = run(result_dir=str(job), field="s_xx")
    shown = [m for m in r.data["preview"]["markers"] if m["visible"]]
    assert shown, "at least one end of the tetrahedron's field is seen from some side"
    for m in r.data["preview"]["markers"]:
        if m["visible"]:
            assert m["view"] in ("iso", "iso_back", "iso_under", "iso_under_back")
        else:
            assert any(f"{m['which']} marker" in w for w in r.warnings)
    assert r.data["preview"]["views"][0] == "iso"


# --------------------------------------------------------------------------- failures


def test_a_picture_that_cannot_be_drawn_is_a_warning_and_the_glb_is_kept(job, monkeypatch):
    def boom(*args, **kwargs):
        raise MemoryError("out of memory in the rasteriser")

    monkeypatch.setattr(viewer.pic, "render_sheet", boom)
    r = run(result_dir=str(job))
    assert r.ok, r.error
    assert names(r) == ["fem_view.glb", "legend.svg", "view.json"]
    assert any("PNG preview was not drawn" in w and "MemoryError" in w for w in r.warnings)
    assert r.data["preview"]["file"] is None


def test_the_pictures_own_time_limit_drops_only_the_picture(job, monkeypatch):
    monkeypatch.setattr(viewer, "PREVIEW_SECONDS", -1.0)
    r = run(result_dir=str(job))
    assert r.ok and any("TimeoutError" in w for w in r.warnings)
    assert names(r) == ["fem_view.glb", "legend.svg", "view.json"]


def test_the_picture_is_written_where_pillow_cannot_be_imported(job, monkeypatch):
    for name in ("PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont", "matplotlib"):
        monkeypatch.setitem(sys.modules, name, None)
    r = run(result_dir=str(job))
    assert r.ok and not any("PNG" in w for w in r.warnings)
    read_png(artifact(r, "fem_view.png").read_bytes())


def test_a_deformed_shape_and_mesh_edges_make_a_valid_picture_too(job):
    r = run(result_dir=str(job), deformation_scale="50", mesh_edges=True, field="displacement")
    assert r.ok, r.error
    read_png(artifact(r, "fem_view.png").read_bytes())
    assert r.data["preview"]["colour_scale"]["unit"] == "mm"


# --------------------------------------------------------------------------- the real cantilever

real = pytest.mark.skipif(not (HAVE_PROGRAMS and HAVE_OCP), reason="needs OCP (uv sync --extra cad), Gmsh and CalculiX installed")


@pytest.fixture(scope="module")
def cantilever(tmp_path_factory):
    """fem_static on a 100 x 10 x 10 mm bar clamped at x = 0 with 1 kN along -z at x = 100, once for the module."""
    if not (HAVE_PROGRAMS and HAVE_OCP):
        pytest.skip("needs OCP, Gmsh and CalculiX")
    import asyncio

    from tests import cad_fixtures as cad
    from tests.test_fem_view import call

    root = tmp_path_factory.mktemp("fem_view_preview_real")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ENG_TOOLKIT_WS", str(root / "ws"))
        model = str(cad.write_step(cad.box(0, 0, 0, 100, 10, 10), root / "cantilever.step"))
        result = asyncio.run(
            call(
                "fem_static",
                {
                    "model_path": model,
                    "youngs_modulus": "210000 MPa",
                    "poisson_ratio": 0.3,
                    "element_size": "2.5 mm",
                    "supports": "x = 0 mm",
                    "loads": "force (0 N, 0 N, -1 kN) on x = 100 mm",
                },
            )
        )
    assert result.ok, result.error
    return result


@real
def test_the_cantilever_deflection_picture_has_the_range_the_ends_and_the_markers_the_numbers_say(cantilever, tmp_path, monkeypatch):
    monkeypatch.setenv("ENG_TOOLKIT_WS", str(tmp_path / "ws"))
    r = run(result_dir=cantilever.data["job_dir"], field="uz", deformation_scale="auto")
    assert r.ok, r.error
    p = r.data["preview"]
    low, high = p["colour_scale"]["low"], p["colour_scale"]["high"]
    assert low == -high and high == pytest.approx(1.9048, rel=0.01), "symmetric about zero, and the beam-theory tip deflection 1.9048 mm"
    by = {m["which"]: m for m in p["markers"]}
    assert by["min"]["value"] == pytest.approx(r.values["field_min"].quantity, rel=1e-6) and by["min"]["value"] < -1.8
    assert by["max"]["value"] == pytest.approx(r.values["field_max"].quantity, rel=1e-6)
    assert by["min"]["visible"] is True, "the tip's lowest corner is on the surface and faces the camera or the view from below"
    image = picture(r)
    bar = colour_bar(image)
    middle = bar[BAR_HEIGHT // 2].astype(int)
    assert max(middle) - min(middle) <= 3 and middle.min() >= 200
    assert p["model_size_mm"] == {"x": 100.0, "y": 10.0, "z": 10.0}


@real
def test_the_cantilever_von_mises_picture_marks_a_maximum_that_may_sit_on_the_underside(cantilever, tmp_path, monkeypatch):
    monkeypatch.setenv("ENG_TOOLKIT_WS", str(tmp_path / "ws"))
    r = run(result_dir=cantilever.data["job_dir"])
    assert r.ok, r.error
    (marker,) = r.data["preview"]["markers"]
    assert marker["which"] == "max" and marker["value"] == pytest.approx(r.values["field_max"].quantity, rel=1e-6)
    assert marker["visible"] is True and marker["view"] is not None
    assert len(r.data["preview"]["views"]) in (1, 2)
