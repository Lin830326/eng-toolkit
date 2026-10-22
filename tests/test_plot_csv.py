"""CSV input: header names, text columns that are not plotted, strict numbers, ragged rows, separators, caps."""

import json

import pytest

from eng_toolkit.domains.plot.panels import panels
from eng_toolkit.domains.plot.xy import xy
from tests.plot_testing import fails, ok, plot_data, polylines, svg_text


def write(tmp_path, name: str, text: str):
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return str(path)


STATIONS = "x_m,side,V_N,M_N_m\n0,right,10,0\n1,right,5,7.5\n2,left,0,10\n2,right,0,10\n4,left,-10,0\n"


def test_named_columns_with_a_text_column_in_the_file(tmp_path):
    path = write(tmp_path, "member_1_stations.csv", STATIONS)
    r = ok(xy, data_file=path, x_column="x_m", y_columns="V_N,M_N_m")
    d = plot_data(r)
    assert [s["column"] for s in d["series"]] == ["V_N", "M_N_m"] and d["series"][0]["x_column"] == "x_m"
    assert r.provenance.input_files[0].path.endswith("member_1_stations.csv")
    assert len(polylines(svg_text(r))) == 2


def test_default_columns_skip_text_columns_and_say_so(tmp_path):
    path = write(tmp_path, "s.csv", STATIONS)
    r = ok(xy, data_file=path)
    assert [s["column"] for s in plot_data(r)["series"]] == ["V_N", "M_N_m"]
    assert any("no numeric cell" in w and "text columns" in w and "side" in w for w in r.warnings)


@pytest.mark.parametrize("text,family,line", [
    ("x,y\n1,2\n2,3,4\n", "input.invalid", 3),            # ragged row
    ("x,y\n1,2\n2,three\n", "input.invalid", 3),          # non-numeric cell
    ("x,y\n1,2\n2,nan\n", "input.invalid", 3),            # NaN text
    ("x,y\n1,2\n2,inf\n", "input.invalid", 3),
    ("x,y\n1,2\n2,1_000\n", "input.invalid", 3),          # underscore
    ("x,y\n1,2\n2,０x10\n", "input.invalid", 3),            # fullwidth / hex
    ("x,y\n1,2\n2,１\n", "input.invalid", 3),               # fullwidth digit
    ("x,y\n1,2\n2,\n", "input.invalid", 3),               # empty cell
    ("x,y\n1,2\n2,1e308\n", "input.out_of_range", 3),     # absurd magnitude
    ("x,y\n1,2\n\n\n5,1e400\n", "input.out_of_range", 5),  # beyond double precision, blank lines counted
])
def test_bad_cells_are_refused_with_line_and_column(tmp_path, text, family, line):
    path = write(tmp_path, "bad.csv", text)
    r = fails(xy, family, None, data_file=path, y_columns="y")
    ragged = "2,3,4" in text  # the ragged row has a third cell
    assert r.error.details["param"] == f"/data_file/{line}" + ("" if ragged else "/y")  # a cell pointer: /data_file/<line>/<column>
    assert r.error.details["line"] == line and f"line {line}" in r.error.message


def test_missing_column_and_file_level_refusals(tmp_path):
    path = write(tmp_path, "a.csv", "x,y\n1,2\n2,3\n")
    r = fails(xy, "input.invalid", "y_columns", data_file=path, y_columns="y,zz")
    assert "'zz'" in r.error.message and "'x'" in r.error.message
    fails(xy, "input.invalid", "x_column", data_file=path, x_column="t")
    fails(xy, "input.invalid", "data_file", data_file=write(tmp_path, "h.csv", "x,y\n"))
    fails(xy, "input.invalid", "data_file", data_file=write(tmp_path, "e.csv", "\n\n"))
    fails(xy, "input.invalid", "data_file", data_file=write(tmp_path, "n.csv", "x,y\n1,\x002\n"))
    fails(xy, "input.unsupported_format", "data_file", data_file=write(tmp_path, "a.json", "x,y\n1,2\n"))
    fails(xy, "input.file_not_found", "data_file", data_file=str(tmp_path / "missing.csv"))
    (tmp_path / "latin.csv").write_bytes("x,y\n1,2\xe9\n".encode("latin-1"))
    fails(xy, "input.invalid", "data_file", data_file=str(tmp_path / "latin.csv"))
    fails(xy, "input.out_of_range", "y_columns", data_file=write(tmp_path, "w.csv", ",".join(f"c{i}" for i in range(23)) + "\n" + ",".join("1" * 23) + "\n"))
    fails(xy, "input.out_of_range", "data_file", data_file=write(tmp_path, "long.csv", "x," + "y" * 300 + "\n1,2\n"))
    fails(xy, "input.out_of_range", "x_column", data_file=path, x_column="q" * 6000)


def test_row_cap(tmp_path):
    body = "\n".join(f"{i},{i}" for i in range(200_001))
    fails(xy, "input.out_of_range", "data_file", data_file=write(tmp_path, "big.csv", "x,y\n" + body + "\n"))


def test_bom_tabs_whitespace_and_quoted_cells(tmp_path):
    for name, text in (
        ("bom.csv", "﻿x,y\n1,2\n2,4\n"),
        ("tab.txt", "x\ty\n1\t2\n2\t4\n"),
        ("ws.txt", "x   y\n1   2\n  2 4\n"),
        ("quoted.csv", 'x,"y"\n"1","2"\n2,4\n'),
    ):
        r = ok(xy, data_file=write(tmp_path, name, text), y_columns="y")
        assert plot_data(r)["series"][0]["y_max"] == 4.0, name


def test_log_axis_on_a_csv_column_points_at_the_column_and_line(tmp_path):
    path = write(tmp_path, "f.csv", "frequency_hz,magnitude_db\n0,0\n10,-1\n")
    r = fails(xy, "input.invalid", "data_file/2/frequency_hz", data_file=path, x_scale="log")
    assert r.error.details["column"] == "frequency_hz" and r.error.details["line"] == 2
    assert "frequency_hz" in r.error.message and "line 2" in r.error.message


def test_panels_from_csv_share_x(tmp_path):
    path = write(tmp_path, "bode.csv", "frequency_hz,magnitude_db,phase_deg\n" + "\n".join(f"{f},{-f / 100},{-f / 10}" for f in range(0, 1001, 10)) + "\n")
    r = ok(panels, data_file=path, panels="Gain|magnitude_db|dB;Phase|phase_deg|deg|linear", x_label="Frequency (Hz)")
    d = plot_data(r)
    assert [p["title"] for p in d["panels"]] == ["Gain", "Phase"]
    assert d["panels"][0]["axes"]["x"]["mapping"] == d["panels"][1]["axes"]["x"]["mapping"]
    assert r.value.quantity == 2 * 101 and r.values["panels"].quantity == 2
    fails(panels, "input.invalid", "panels/1", data_file=path, panels="Gain|magnitude_db;Phase|nope")
    assert json.dumps(r.data, allow_nan=False)
