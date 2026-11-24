"""pcb_export, round 3: the board fills the page, one file per layer on request, and the fab package carries the board's name and its drill file.

Judges E1-2 / E1-5 of the second user-acceptance run: the board was a 75 x 180 px picture on a 1350 x 960 px page, and the gerber zip had no drill file and
was named ``board-*``.  The tests with the real kicad-cli skip when it is not installed; the StickHub demo board is used when KiCad's demo folder is there.
"""

import io
import re
import zipfile
from pathlib import Path

import pytest

from eng_toolkit.domains.pcb import program
from tests.pcb_testing import call, fixture, needs_kicad, v

STICKHUB = Path("C:/Users/USER/AppData/Local/Programs/KiCad/10.0/share/kicad/demos/stickhub/StickHub.kicad_pcb")
needs_stickhub = pytest.mark.skipif(not STICKHUB.is_file(), reason="the KiCad demo board StickHub is not installed here")


# --------------------------------------------------------------------------- the name of the board (no KiCad needed)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("C:/boards/StickHub.kicad_pcb", "StickHub"),
        ("C:\\boards\\My Board v2.kicad_pcb", "My_Board_v2"),
        ("/home/u/usb-hub.kicad_pcb", "usb-hub"),
        ("x; calc & dir $(id) `id` %PATH% 板 -o --help.kicad_pcb", "x_calc_dir_id_id_PATH_o_help"),
        ("--help.kicad_pcb", "help"),
        ("板.kicad_pcb", "board"),  # nothing ASCII is left
        ("....kicad_pcb", "board"),
        ("CON.kicad_pcb", "board_CON"),  # a Windows device name
        ("nul.kicad_pcb", "board_nul"),
        ("a" * 100 + ".kicad_pcb", "a" * 48),
        ("", "board"),
    ],
)
def test_safe_stem_keeps_only_a_safe_alphabet(raw, expected):
    assert program.safe_stem(raw) == expected
    assert re.fullmatch(r"[A-Za-z0-9_.]+(-[A-Za-z0-9_.]+)*", program.safe_stem(raw))


def test_safe_stem_of_a_non_string_is_board():
    assert program.safe_stem(None) == "board" and program.safe_stem(5) == "board"


# --------------------------------------------------------------------------- with the real kicad-cli


@needs_kicad
@pytest.mark.asyncio
async def test_the_gerber_zip_holds_the_drill_file_and_the_job_file_under_the_board_name():
    r = await call("pcb_export", board_path=fixture("clean.kicad_pcb"), export="gerbers")
    assert r.ok, r.error
    names = {f["name"] for f in r.data["files"]}
    assert {"clean.drl", "clean-job.gbrjob", "clean-F_Cu.gtl", "clean-gerbers.zip"} <= names
    assert not any(n.startswith("board") for n in names)
    zip_path = next(a.path for a in r.artifacts if a.path.endswith("clean-gerbers.zip"))
    with zipfile.ZipFile(io.BytesIO(Path(zip_path).read_bytes())) as archive:
        inside = set(archive.namelist())
        drill = archive.read("clean.drl").decode("utf-8")
        job = archive.read("clean-job.gbrjob").decode("utf-8")
    assert inside == names - {"clean-gerbers.zip"}
    assert "T1C0.400" in drill and "T2C0.600" in drill  # the via and the through-hole pad of the fixture, see test_pcb_kicad
    # the job file names the same board; every gerber it lists is a file of the zip
    assert '"Name": "clean"' in job
    listed = re.findall(r'"Path": "([^"]+)"', job)
    assert listed and set(listed) <= inside, (listed, inside)
    assert "the board has no drilled hole" not in " ".join(r.warnings)


@needs_kicad
@needs_stickhub
@pytest.mark.asyncio
async def test_stickhub_fab_package_is_one_zip_with_everything_a_fabricator_asks_for():
    r = await call("pcb_export", board_path=str(STICKHUB), export="gerbers")
    assert r.ok, r.error
    zip_path = next(a.path for a in r.artifacts if a.path.endswith("StickHub-gerbers.zip"))
    with zipfile.ZipFile(zip_path) as archive:
        inside = archive.namelist()
    assert "StickHub.drl" in inside and "StickHub-job.gbrjob" in inside
    for ext in ("-F_Cu.gtl", "-B_Cu.gbl", "-F_Mask.gts", "-B_Mask.gbs", "-F_Silkscreen.gto", "-Edge_Cuts.gm1"):
        assert f"StickHub{ext}" in inside, ext
    assert all(n.startswith("StickHub") for n in inside)


@needs_kicad
@pytest.mark.asyncio
async def test_svg_is_cropped_to_the_board_and_named_after_it():
    r = await call("pcb_export", board_path=fixture("clean.kicad_pcb"), export="svg", layers="F.Cu,Edge.Cuts")
    assert r.ok, r.error
    text = Path(next(a.path for a in r.artifacts if a.path.endswith("clean.svg"))).read_text(encoding="utf-8")
    # the outline is (0,0)-(20,20): the page is the board (about 20 mm), not an A4 sheet (297 mm)
    width = float(re.search(r'width="([0-9.]+)mm"', text).group(1))
    height = float(re.search(r'height="([0-9.]+)mm"', text).group(1))
    assert 19.5 <= width <= 22.0 and 19.5 <= height <= 22.0, (width, height)
    assert "board area only" in r.data["page"]["mode"]


def _tall_board(tmp_path: Path) -> Path:
    text = Path(fixture("clean.kicad_pcb")).read_text(encoding="utf-8")
    assert text.count("(end 20 20)") == 1
    path = tmp_path / "tall_board.kicad_pcb"
    path.write_text(text.replace("(end 20 20)", "(end 20 60)"), encoding="utf-8")  # 20 x 60 mm
    return path


def _media_box(pdf: bytes) -> tuple[float, float]:
    found = re.search(rb"/MediaBox\s*\[\s*([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s*\]", pdf)
    assert found, "no MediaBox in the PDF"
    x0, y0, x1, y1 = (float(g) for g in found.groups())
    return x1 - x0, y1 - y0


@needs_kicad
@pytest.mark.asyncio
async def test_pdf_page_is_turned_to_the_shape_of_the_board_and_the_file_is_named_after_it(tmp_path):
    tall = _tall_board(tmp_path)
    r = await call("pcb_export", board_path=str(tall), export="pdf", layers="F.Cu,Edge.Cuts")
    assert r.ok, r.error
    pdf = Path(next(a.path for a in r.artifacts if a.path.endswith("tall_board.pdf"))).read_bytes()
    w, h = _media_box(pdf)
    assert h > w, (w, h)  # a 20 x 60 mm board on a portrait page (A4 landscape in the file)
    assert "portrait" in r.data["page"]["mode"] and "--scale 0" in r.data["page"]["mode"]
    wide = await call("pcb_export", board_path=fixture("clean.kicad_pcb"), export="pdf", layers="F.Cu,Edge.Cuts")  # a square board keeps landscape
    w2, h2 = _media_box(Path(next(a.path for a in wide.artifacts if a.path.endswith("clean.pdf"))).read_bytes())
    assert w2 > h2
    # the caller's board is not changed by the page change
    assert 'paper "A4")' in tall.read_text(encoding="utf-8")


@needs_kicad
@needs_stickhub
@pytest.mark.asyncio
async def test_stickhub_board_picture_fills_much_more_of_the_page_than_the_one_to_one_plot():
    r = await call("pcb_export", board_path=str(STICKHUB), export="pdf")
    assert r.ok, r.error
    assert any(a.path.endswith("StickHub.pdf") for a in r.artifacts)
    svg = await call("pcb_export", board_path=str(STICKHUB), export="svg")
    text = Path(next(a.path for a in svg.artifacts if a.path.endswith("StickHub.svg"))).read_text(encoding="utf-8")
    width = float(re.search(r'width="([0-9.]+)mm"', text).group(1))
    assert width < 40.0  # the page is the board (about 17 mm), not a 297 mm sheet


@needs_kicad
@pytest.mark.asyncio
async def test_per_layer_gives_one_svg_per_layer_with_the_outline_on_each():
    r = await call("pcb_export", board_path=fixture("clean.kicad_pcb"), export="svg", layers="F.Cu,B.Cu,Edge.Cuts", per_layer=True)
    assert r.ok, r.error
    assert sorted(f["name"] for f in r.data["files"]) == ["clean-B_Cu.svg", "clean-F_Cu.svg"]
    assert v(r, "file_count") == 2
    for artifact in r.artifacts:
        if artifact.path.endswith(".svg"):
            text = Path(artifact.path).read_text(encoding="utf-8")
            # every file is cropped to the board outline (20 mm)
            assert 19.5 <= float(re.search(r'width="([0-9.]+)mm"', text).group(1)) <= 22.0
    pdf = await call("pcb_export", board_path=fixture("clean.kicad_pcb"), export="pdf", layers="F.Cu,B.Cu,Edge.Cuts", per_layer=True)
    assert pdf.ok, pdf.error
    assert sorted(f["name"] for f in pdf.data["files"]) == ["clean-B_Cu.pdf", "clean-F_Cu.pdf"]
    assert pdf.data["per_layer"] == ["clean-B_Cu.pdf", "clean-F_Cu.pdf"]


@needs_kicad
@pytest.mark.asyncio
async def test_per_layer_is_ignored_with_a_warning_where_it_means_nothing():
    r = await call("pcb_export", board_path=fixture("clean.kicad_pcb"), export="drill", per_layer=True)
    assert r.ok, r.error
    assert any("per_layer is ignored" in w for w in r.warnings)
    one = await call("pcb_export", board_path=fixture("clean.kicad_pcb"), export="svg", layers="Edge.Cuts", per_layer=True)
    assert one.ok and [f["name"] for f in one.data["files"]] == ["clean-Edge_Cuts.svg"]


@pytest.mark.asyncio
async def test_per_layer_must_be_a_boolean():
    r = await call("pcb_export", board_path=fixture("clean.kicad_pcb"), export="svg", per_layer="maybe")
    assert r.ok is False and r.error.family.startswith("input.")
