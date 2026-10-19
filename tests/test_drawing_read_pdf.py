"""The PDF reader on hand-written PDF bytes (tests/raw_pdf.py): known strings at known places, known line lengths, hostile files.

Second opinions come from engines the reader does not use: pypdfium2 (PDFium, native) for the text.
"""

import math
from pathlib import Path

import pytest
import raw_pdf as R

from eng_toolkit.errors import ToolFailure
from eng_toolkit.isolated import run_isolated

pytest.importorskip("pdfminer", reason="pdfminer.six is not installed; run: uv sync --extra drawing")
from eng_toolkit.domains.drawing import pdf_read, reading  # noqa: E402

MM = 25.4 / 72.0
HERE = str(Path(__file__).resolve().parent)


def read(tmp_path, payload: bytes, *, name="t.pdf", limits=None):
    path = tmp_path / name
    path.write_bytes(payload)
    return pdf_read.read(str(path), limits or reading.Limits().as_dict())


def main_pdf():
    return R.document([(R.MAIN, R.A4, 0)])


def lines_of(r, page=0):
    return {line["text"]: line for line in r["pages"][page]["text"]["lines"]}


# --------------------------------------------------------------------------- pages


def test_the_page_size_is_in_millimetres(tmp_path):
    r = read(tmp_path, main_pdf())
    assert r["pdf"]["pages_total"] == 1 and r["pdf"]["pages_read"] == 1
    p = r["pages"][0]
    assert (p["width_mm"], p["height_mm"]) == (pytest.approx(210.0, abs=0.01), pytest.approx(297.0, abs=0.01))
    assert r["pdf"]["version"] == "1.4"


def test_pages_of_different_sizes_and_a_rotated_page(tmp_path):
    r = read(tmp_path, R.document([(b"", R.A4, 0), (b"", R.A3_LANDSCAPE, 0), (b"", R.A4, 90)]))
    sizes = [(p["width_mm"], p["height_mm"]) for p in r["pages"]]
    assert sizes[0] == pytest.approx((210.0, 297.0), abs=0.01)
    assert sizes[1] == pytest.approx((420.0, 297.0), abs=0.01)
    assert sizes[2] == pytest.approx((297.0, 210.0), abs=0.01)
    assert r["pages"][2]["rotation"] == 90


def test_pages_beyond_the_limit_are_not_read_and_the_warning_says_how_many(tmp_path):
    payload = R.document([(R.MAIN, R.A4, 0)] * 5)
    r = read(tmp_path, payload, limits=reading.Limits(max_pages=2).as_dict())
    assert r["pdf"]["pages_total"] == 5 and r["pdf"]["pages_read"] == 2 and len(r["pages"]) == 2
    assert any("2 of 5 pages" in w for w in r["warnings"])


# --------------------------------------------------------------------------- text


def test_known_strings_come_back_with_their_positions_in_millimetres(tmp_path):
    r = read(tmp_path, main_pdf())
    lines = lines_of(r)
    assert set(lines) >= {"HELLO FL-001", "MATERIAL AL6061"}
    hello = lines["HELLO FL-001"]
    x0, y0, x1, y1 = hello["bbox_mm"]
    assert x0 == pytest.approx(72 * MM, abs=0.3)  # the text is placed at x = 72 pt
    assert y0 < 700 * MM < y1 + 1.0  # on the baseline at y = 700 pt
    assert hello["size_pt"] == pytest.approx(12.0, abs=0.1)
    assert lines["MATERIAL AL6061"]["size_pt"] == pytest.approx(10.0, abs=0.1)
    assert r["text"]["has_text_layer"] is True and r["text"]["chars"] >= len("HELLO FL-001MATERIAL AL6061")
    assert y1 > lines["MATERIAL AL6061"]["bbox_mm"][3]  # origin at the bottom: the line at 700 pt is above the one at 650 pt


def sizes(tmp_path, content: str) -> list[float]:
    page = R.document([(content.encode(), R.A4, 0)])
    return [line["size_pt"] for line in read(tmp_path, page)["pages"][0]["text"]["lines"]]


@pytest.mark.parametrize(
    ("content", "size"),
    [
        ("BT /F1 12 Tf 72 600 Td (PLAIN) Tj ET", 12.0),
        ("BT /F1 1 Tf 12 0 0 12 72 600 Tm (TM) Tj ET", 12.0),  # Tf size x text matrix scale
        ("q 12 0 0 12 0 0 cm BT /F1 1 Tf 6 50 Td (CM) Tj ET Q", 12.0),  # Tf size x current transformation matrix
        ("q 2 0 0 2 0 0 cm BT /F1 3 Tf 2 0 0 2 10 100 Tm (BOTH) Tj ET Q", 12.0),  # 3 x 2 x 2
        ("BT /F1 12 Tf 50 Tz 72 600 Td (HSCALE) Tj ET", 12.0),  # horizontal scaling is not the size
        ("BT /F1 1 Tf 12 0 0 6 72 600 Tm (FLAT) Tj ET", 6.0),  # the size is the height of the em box
        ("BT /F1 1 Tf 0 12 -12 0 300 100 Tm (ROTATED) Tj ET", 12.0),  # turned 90 degrees: not the width of each glyph
        ("BT /F1 1 Tf 10.3923 6 -6 10.3923 100 100 Tm (THIRTY) Tj ET", 12.0),  # turned 30 degrees
        ("BT /F1 2 Tf 0 -9 9 0 300 700 Tm (DOWN) Tj ET", 18.0),  # turned 270 degrees
    ],
)
def test_the_font_size_is_the_font_size_times_the_text_and_current_matrices(tmp_path, content, size):
    found = sizes(tmp_path, content)
    assert found and all(value == pytest.approx(size, abs=0.05) for value in found), found


def test_lines_come_in_reading_order_top_to_bottom_then_left_to_right(tmp_path):
    content = b"BT /F1 12 Tf 300 400 Td (RIGHT) Tj ET BT /F1 12 Tf 72 400 Td (LEFT) Tj ET BT /F1 12 Tf 72 700 Td (TOP) Tj ET BT /F1 12 Tf 72 100 Td (BOTTOM) Tj ET"
    r = read(tmp_path, R.document([(content, R.A4, 0)]))
    assert [line["text"] for line in r["pages"][0]["text"]["lines"]] == ["TOP", "LEFT", "RIGHT", "BOTTOM"]


def test_pypdfium2_reads_the_same_strings(tmp_path):
    pdfium = pytest.importorskip("pypdfium2")
    path = tmp_path / "t.pdf"
    path.write_bytes(main_pdf())
    document = pdfium.PdfDocument(str(path))
    text = document[0].get_textpage().get_text_range()
    assert "HELLO FL-001" in text and "MATERIAL AL6061" in text


def test_pdfplumber_agrees_on_the_words_and_the_rectangle(tmp_path):
    plumber = pytest.importorskip("pdfplumber")
    path = tmp_path / "t.pdf"
    path.write_bytes(main_pdf())
    r = pdf_read.read(str(path), reading.Limits().as_dict())
    with plumber.open(str(path)) as document:
        words = [w["text"] for w in document.pages[0].extract_words()]
        assert words == ["HELLO", "FL-001", "MATERIAL", "AL6061"]
        assert len(document.pages[0].rects) == 1
    assert r["pages"][0]["vector"]["rects"] == 1


def test_a_page_without_text_says_there_is_no_text_layer_and_does_not_pretend(tmp_path):
    r = read(tmp_path, R.document([(b"0 0 m 100 0 l S\n10 10 m 20 10 l 20 20 l h f\n", R.A4, 0)]))
    assert r["text"]["has_text_layer"] is False and r["text"]["chars"] == 0
    assert r["pages"][0]["text"]["lines"] == []
    assert any("no text layer" in w and "outline" in w for w in r["warnings"])


# --------------------------------------------------------------------------- vector content


def bezier_length(p0, p1, p2, p3, steps=20000):
    def point(t):
        u = 1 - t
        return tuple(u**3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t**3 * d for a, b, c, d in zip(p0, p1, p2, p3))

    pts = [point(i / steps) for i in range(steps + 1)]
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def test_vector_statistics_are_worked_out_from_the_operators(tmp_path):
    v = read(tmp_path, main_pdf())["pages"][0]["vector"]
    assert v["paths"] == 5 and v["stroked"] == 4 and v["filled"] == 1 and v["rects"] == 1
    assert v["straight_length_mm"] == pytest.approx((200 + 100 + 240) * MM, abs=0.01)
    assert v["curve_length_mm"] == pytest.approx(bezier_length((200, 400), (250, 450), (300, 450), (350, 400)) * MM, rel=2e-3)
    x0, y0, x1, y1 = v["bbox_mm"]
    assert (x0, y0) == pytest.approx((50 * MM, 100 * MM), abs=0.05)
    assert (x1, y1) == pytest.approx((410 * MM, 437.5 * MM), abs=0.05)


def test_line_styles_are_grouped_by_width_and_dash(tmp_path):
    styles = {(s["width_pt"], s["dashed"]): s for s in read(tmp_path, main_pdf())["pages"][0]["vector"]["by_style"]}
    assert styles[(0.5, False)]["length_mm"] == pytest.approx(200 * MM, abs=0.01) and styles[(0.5, False)]["paths"] == 1
    assert styles[(0.5, True)]["length_mm"] == pytest.approx(100 * MM, abs=0.01)
    assert styles[(1.0, False)]["length_mm"] == pytest.approx(240 * MM, abs=0.01)
    assert (0.25, False) in styles  # the Bezier
    assert all(not s["dashed"] for key, s in styles.items() if key[0] != 0.5)


def test_the_totals_add_up_over_pages(tmp_path):
    r = read(tmp_path, R.document([(R.MAIN, R.A4, 0), (R.MAIN, R.A4, 0)]))
    assert r["vector"]["paths"] == 10 and r["vector"]["straight_length_mm"] == pytest.approx(2 * 540 * MM, abs=0.02)
    assert r["text"]["chars"] > 0 and len(r["pages"]) == 2


# --------------------------------------------------------------------------- active content: reported, never run or followed


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("javascript", {"JavaScript", "OpenAction"}), ("obfuscated-javascript", {"JavaScript"}), ("uri", {"URI"}), ("launch", {"Launch", "OpenAction"}), ("embedded", {"EmbeddedFile"})],
)
def test_active_content_is_named_in_the_result_and_a_warning(tmp_path, kind, expected):
    r = read(tmp_path, R.active_content(kind))
    assert expected <= set(r["pdf"]["active_content"]), r["pdf"]["active_content"]
    assert any("not executed" in w and "not followed" in w for w in r["warnings"])


def test_javascript_inside_a_compressed_object_stream_is_found_by_parsing(tmp_path):
    payload = R.hidden_in_object_stream()
    assert b"JavaScript" not in payload  # not visible in the bytes
    r = read(tmp_path, payload)
    assert "JavaScript" in r["pdf"]["active_content"]


def test_a_clean_file_reports_no_active_content(tmp_path):
    r = read(tmp_path, main_pdf())
    assert r["pdf"]["active_content"] == {}
    assert not any("not executed" in w for w in r["warnings"])


def test_a_uri_link_target_is_not_repeated_in_a_warning(tmp_path):
    r = read(tmp_path, R.active_content("uri"))
    assert not any("evil.example" in w for w in r["warnings"])


# --------------------------------------------------------------------------- damaged and hostile files


def test_a_truncated_pdf_gives_what_it_can_or_a_structured_error(tmp_path):
    payload = main_pdf()
    try:
        r = read(tmp_path, payload[: int(len(payload) * 0.7)])
    except ToolFailure as exc:
        assert exc.family == "drawing.corrupt"
    else:
        assert r["pdf"]["pages_total"] >= 0


@pytest.mark.parametrize("payload", [b"", b"%PDF-1.4\n", b"\x00\x01garbage" * 100, b"just text, not a pdf at all\n"])
def test_garbage_is_a_structured_error(tmp_path, payload):
    path = tmp_path / "bad.pdf"
    path.write_bytes(payload)
    with pytest.raises(ToolFailure) as caught:
        pdf_read.read(str(path), reading.Limits().as_dict())
    assert caught.value.family == "drawing.corrupt"


def test_a_dxf_named_pdf_is_refused(tmp_path):
    path = tmp_path / "x.pdf"
    path.write_bytes(b"  0\r\nSECTION\r\n  2\r\nHEADER\r\n")
    with pytest.raises(ToolFailure) as caught:
        pdf_read.read(str(path), reading.Limits().as_dict())
    assert caught.value.family == "input.invalid" and "DXF" in caught.value.message


def test_a_file_over_the_size_limit_is_refused_before_it_is_parsed(tmp_path):
    path = tmp_path / "x.pdf"
    path.write_bytes(main_pdf())
    with pytest.raises(ToolFailure) as caught:
        pdf_read.read(str(path), reading.Limits(max_file_bytes=100).as_dict())
    assert caught.value.family == "drawing.too_large" and caught.value.details["limit_bytes"] == 100


def test_a_page_tree_that_lists_itself_does_not_loop(tmp_path):
    r = read(tmp_path, R.cyclic_pages())
    assert r["pdf"]["pages_total"] >= 1


def test_a_form_that_draws_itself_is_a_structured_result_or_error(tmp_path):
    try:
        r = read(tmp_path, R.self_referencing_form())
    except ToolFailure as exc:
        assert exc.family == "drawing.corrupt"
    else:
        assert len(r["pages"]) == 1


def test_a_password_protected_file_is_a_structured_error(tmp_path, monkeypatch):
    from pdfminer import pdfdocument

    def locked(*args, **kwargs):
        raise pdfdocument.PDFPasswordIncorrect()

    monkeypatch.setattr(pdfdocument, "PDFDocument", locked)
    monkeypatch.setattr(pdf_read, "_open_document", lambda parser: locked())
    with pytest.raises(ToolFailure) as caught:
        read(tmp_path, main_pdf())
    assert caught.value.family == "drawing.encrypted"


def test_a_stream_that_expands_a_thousand_fold_is_stopped_by_the_child_processs_memory_limit(tmp_path):
    path = tmp_path / "bomb.pdf"
    path.write_bytes(R.flate_bomb(2500))  # about 2.4 MB on disk, 2.5 GB of spaces when inflated
    assert path.stat().st_size < 10_000_000
    with pytest.raises(ToolFailure) as caught:
        run_isolated("eng_toolkit.domains.drawing.pdf_read:read", [str(path), reading.Limits().as_dict()], timeout_s=120.0, memory_mb=400)
    assert caught.value.family in ("engine.resource_limit", "engine.worker_died")


def test_nothing_is_written_to_stdout_by_the_reader(tmp_path, capfd):
    read(tmp_path, main_pdf())
    out, err = capfd.readouterr()
    assert out == ""
