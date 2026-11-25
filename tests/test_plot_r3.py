"""Round-3 findings of the plot tools (user-acceptance round 2): unwrapped Bode phase, SI-prefixed seconds ticks, one style of log ticks,
a legend text budget that grows with the chart, and hlines on bar charts.

Each test uses the exact input that failed: a CE-amplifier Bode phase that wraps at +-180 deg (E2-4), a log frequency axis of
10 Hz..100 MHz with mixed '100000' and '1e6' tick labels and the legend entry 'v(nout).phase_deg (right axis)' cut to
'v(nout).phase_deg (right a...' (E2-5), and a 2.100..2.106 ms switching window labelled '2.1e-3 .. 2.106e-3' (E4-5).
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from eng_toolkit.domains.plot import _axes
from eng_toolkit.domains.plot._chart import LEGEND_TEXT_MAX, LEGEND_TEXT_WIDEST, legend_text_budget
from eng_toolkit.domains.plot.bar import bar
from eng_toolkit.domains.plot.panels import panels
from eng_toolkit.domains.plot.xy import xy
from tests.plot_testing import fails, ok, plot_data, polylines, svg_text, texts

F = np.logspace(1, 8, 120)
H = -(1j * F / 100.0) / (1 + 1j * F / 100.0) / (1 + 1j * F / 3e6) * 40  # inverting band-pass stage: the phase passes -180 deg
GAIN = 20 * np.log10(np.abs(H))
WRAPPED = np.degrees(np.angle(H))  # in (-180, 180]: jumps from -180 to +180 in the mid-band
UNWRAPPED = np.unwrap(WRAPPED, period=360.0)
BODE = json.dumps([{"name": "gain_db", "x": F.tolist(), "y": GAIN.tolist()}, {"name": "v(nout).phase_deg", "x": F.tolist(), "y": WRAPPED.tolist()}])
BODE_PANELS = "Gain|gain_db|dB;Phase|v(nout).phase_deg|deg"


def biggest_step(svg: str) -> float:
    return max(abs(b[1] - a[1]) for line in polylines(svg) for a, b in zip(line, line[1:]))


def csv(tmp_path, header: str, rows) -> str:
    path = tmp_path / "bode.csv"
    path.write_text(header + "\n" + "\n".join(",".join(f"{v!r}" for v in row) for row in rows) + "\n", encoding="utf-8")
    return str(path)


# ----------------------------------------------------------------------------------------------- unwrap_phase (E2-4)


def test_the_wrapped_phase_is_drawn_with_a_vertical_jump_by_default_and_is_continuous_with_unwrap_phase():
    assert WRAPPED.max() > 170 and WRAPPED.min() < -170  # the fixture really wraps
    plain = ok(panels, panels=BODE_PANELS, series=BODE, x_scale="log", format="svg")
    fixed = ok(panels, panels=BODE_PANELS, series=BODE, x_scale="log", unwrap_phase=True, format="svg")
    assert biggest_step(svg_text(plain)) > 150  # a line across the panel
    assert biggest_step(svg_text(fixed)) < 40
    phase = lambda r: next(s for s in plot_data(r)["series"] if s["name"] == "v(nout).phase_deg")  # noqa: E731
    assert phase(plain)["phase_unwrapped"] is None and phase(plain)["y_min"] == pytest.approx(WRAPPED.min())
    assert phase(fixed)["phase_unwrapped"] == "360 deg"
    assert phase(fixed)["y_min"] == pytest.approx(UNWRAPPED.min()) and phase(fixed)["y_max"] == pytest.approx(UNWRAPPED.max())
    assert phase(fixed)["y_max"] - phase(fixed)["y_min"] < 200  # one continuous curve, not -180..180 with a jump
    gain = next(s for s in plot_data(fixed)["series"] if s["name"] == "gain_db")
    assert gain["phase_unwrapped"] is None and gain["y_max"] == pytest.approx(GAIN.max())  # only a series named phase
    assert any("unwrap_phase" in w and "numpy.unwrap" in w and "v(nout).phase_deg" in w for w in fixed.warnings)
    assert not any("unwrap_phase" in w for w in plain.warnings)


def test_plot_xy_unwraps_a_csv_phase_column_on_the_right_axis(tmp_path):
    path = csv(tmp_path, "frequency_hz,gain_db,v(nout).phase_deg", zip(F.tolist(), GAIN.tolist(), WRAPPED.tolist()))
    args = dict(data_file=path, y_columns="gain_db", y2_columns="v(nout).phase_deg", x_scale="log", format="svg")
    plain, fixed = ok(xy, **args), ok(xy, unwrap_phase=True, **args)
    assert biggest_step(svg_text(plain)) > 150 and biggest_step(svg_text(fixed)) < 40
    y2 = plot_data(fixed)["panels"][0]["axes"]["y2"]
    assert y2["min"] < UNWRAPPED.min() + 1e-9 and y2["max"] > UNWRAPPED.max() - 1e-9 and y2["max"] - y2["min"] < 400
    assert plot_data(plain)["panels"][0]["axes"]["y2"]["min"] <= -180 and plot_data(plain)["panels"][0]["axes"]["y2"]["max"] >= 180


def test_a_phase_in_radians_is_unwrapped_with_a_period_of_two_pi():
    rad = np.angle(H)
    series = json.dumps([{"name": "phase_rad", "x": F.tolist(), "y": rad.tolist()}])
    r = ok(xy, series=series, x_scale="log", unwrap_phase=True, format="svg")
    s = plot_data(r)["series"][0]
    assert s["phase_unwrapped"] == "2*pi rad" and s["y_min"] == pytest.approx(np.unwrap(rad).min()) and s["y_max"] == pytest.approx(np.unwrap(rad).max())
    assert biggest_step(svg_text(r)) < 40


def test_unwrap_phase_without_a_phase_series_says_so_and_changes_nothing():
    series = json.dumps([{"name": "gain_db", "x": [1, 2, 3], "y": [0, 5, 3]}])
    plain, flagged = ok(xy, series=series, format="svg"), ok(xy, series=series, unwrap_phase=True, format="svg")
    assert svg_text(plain) == svg_text(flagged)
    assert any("no series name contains 'phase'" in w for w in flagged.warnings)


def test_unwrap_phase_must_be_true_or_false():
    fails(xy, "input.invalid", "unwrap_phase", series=json.dumps([{"name": "p", "x": [1, 2], "y": [1, 2]}]), unwrap_phase="yes")
    fails(panels, "input.invalid", "unwrap_phase", panels="A|a;B|b", unwrap_phase="yes")


# ----------------------------------------------------------------------------------------------- seconds axes (E4-5)

T = np.linspace(2.100e-3, 2.106e-3, 61)
SWITCH = json.dumps([{"name": "v(sw)", "x": T.tolist(), "y": (12 * (np.sin(2 * np.pi * 500e3 * T) > 0)).tolist()}])


def test_a_microsecond_window_gets_one_si_prefix_on_its_ticks_and_the_result_says_so():
    r = ok(xy, series=SWITCH, x_label="Time (s)", y_label="V (V)", format="svg")
    x = plot_data(r)["panels"][0]["axes"]["x"]
    assert x["tick_labels"] == ["2.1 ms", "2.101 ms", "2.102 ms", "2.103 ms", "2.104 ms", "2.105 ms", "2.106 ms"]
    assert x["ticks"] == pytest.approx([2.1e-3, 2.101e-3, 2.102e-3, 2.103e-3, 2.104e-3, 2.105e-3, 2.106e-3])  # data units stay seconds
    assert x["min"] == pytest.approx(2.1e-3) and x["max"] == pytest.approx(2.106e-3)
    assert x["si_prefix"]["prefix"] == "m" and x["si_prefix"]["unit"] == "ms" and x["si_prefix"]["factor_s"] == 1e-3
    assert any("x axis" in w and "tick labels are in ms" in w for w in r.warnings)
    labels = texts(svg_text(r))
    assert "2.102 ms" in labels and not any("e-3" in t for t in labels) and "Time (s)" in labels  # the caller's title is untouched


@pytest.mark.parametrize(
    ("label", "hi", "expected"),
    [("Time (s)", 5e-4, ["0 us", "100 us", "200 us", "300 us", "400 us", "500 us"]), ("Time [s]", 0.5, ["0 ms", "100 ms", "200 ms", "300 ms", "400 ms", "500 ms"]),
     ("Time, s", 5e-9, ["0 ns", "1 ns", "2 ns", "3 ns", "4 ns", "5 ns"])],
)
def test_the_prefix_follows_the_range_and_the_unit_forms_of_the_title(label, hi, expected):
    series = json.dumps([{"name": "a", "x": [0, hi], "y": [0, 1]}])
    x = plot_data(ok(xy, series=series, x_label=label, x_range=f"0,{hi!r}", format="svg"))["panels"][0]["axes"]["x"]
    assert x["tick_labels"] == expected


@pytest.mark.parametrize(
    ("label", "lo", "hi", "scale"),
    [("Time (ms)", 0, 0.5, "linear"), ("Time", 0, 0.5, "linear"), ("Frequency (Hz)", 0, 0.5, "linear"), ("Time (s)", 0, 2, "linear"), ("Time (s)", 0, 1, "linear"),
     ("Time (s)", 1e-6, 1e-3, "log"), ("", 0, 0.5, "linear")],
)
def test_other_axes_keep_their_plain_labels(label, lo, hi, scale):
    series = json.dumps([{"name": "a", "x": [lo or hi / 100, hi], "y": [1, 2]}])
    x = plot_data(ok(xy, series=series, x_label=label, x_scale=scale, format="svg"))["panels"][0]["axes"]["x"]
    assert "si_prefix" not in x and not any(t.endswith(("ms", "us", "ns", "ps")) for t in x["tick_labels"])


def test_a_seconds_y_axis_of_a_panel_and_the_right_axis_get_the_prefix_too():
    t = [0.0, 1e-4, 2e-4, 3e-4]
    series = json.dumps([{"name": "delay", "x": t, "y": [2e-6, 4e-6, 3e-6, 5e-6]}, {"name": "lag", "x": t, "y": [1e-3, 2e-3, 3e-3, 2e-3]}])
    r = ok(xy, series=series, y2_columns="lag", x_label="Time (s)", y_label="Delay (s)", y2_label="Lag (s)", format="svg")
    axes = plot_data(r)["panels"][0]["axes"]
    assert axes["x"]["si_prefix"]["unit"] == "us" and axes["y"]["si_prefix"]["unit"] == "us" and axes["y2"]["si_prefix"]["unit"] == "ms"
    assert all(lab.endswith(" us") for lab in axes["y"]["tick_labels"]) and all(lab.endswith(" ms") for lab in axes["y2"]["tick_labels"])
    shown = panels  # plot_panels takes the same path
    two = ok(shown, panels="A|a|Delay (s);B|b|Lag (s)", series=json.dumps([{"name": "a", "x": t, "y": [2e-6, 4e-6, 3e-6, 5e-6]}, {"name": "b", "x": t, "y": [1, 2, 3, 2]}]), format="svg")
    assert "si_prefix" in plot_data(two)["panels"][0]["axes"]["y"] and "si_prefix" not in plot_data(two)["panels"][1]["axes"]["y"]


# ----------------------------------------------------------------------------------------------- log ticks in one style (E2-5)


def test_log_ticks_do_not_mix_plain_and_exponent_labels():
    series = json.dumps([{"name": "a", "x": [10, 1e8], "y": [1, 2]}])
    labels = plot_data(ok(xy, series=series, x_scale="log", x_label="Frequency (Hz)", format="svg"))["panels"][0]["axes"]["x"]["tick_labels"]
    assert labels == ["10", "100", "1k", "10k", "100k", "1M", "10M", "100M"]


def test_log_label_styles():
    assert _axes.log_labels([1, 10, 100, 1000, 10000, 100000]) == ["1", "10", "100", "1000", "10000", "100000"]  # all plain: as before
    assert _axes.log_labels([1e6, 1e7, 1e8]) == ["1e6", "1e7", "1e8"]  # all exponent: as before
    assert _axes.log_labels([1e4, 1e5, 1e6, 1e7]) == ["10k", "100k", "1M", "10M"]
    assert _axes.log_labels([1e5, 2e5, 5e5, 1e6]) == ["100k", "200k", "500k", "1M"]
    assert _axes.log_labels([1e-6, 1e-3, 1.0, 1e3]) == ["1e-6", "1e-3", "1e0", "1e3"]
    assert _axes.log_labels([1e-5, 1e-4, 1e-3]) == ["1e-5", "1e-4", "1e-3"]  # '1e-5' needs an exponent, so the plain '0.0001' follows it
    assert [_axes.read_label(x) for x in ("100k", "1M", "2G", "0.5", "1e-6")] == [1e5, 1e6, 2e9, 0.5, 1e-6]
    ax = _axes.make_axis("log", 1e5, 3e6, 8, None, param="x", what="x axis")
    assert ax.labels == ["100k", "200k", "500k", "1M", "2M", "5M", "10M"]  # verified_labels accepts the suffix: each label reads back as its tick


# ----------------------------------------------------------------------------------------------- legend text budget (E2-5)

LONG = "v(nout).phase_deg"
TWO_AXES = json.dumps([{"name": "v(sw)", "x": [0, 1, 2], "y": [1, 2, 3]}, {"name": LONG, "x": [0, 1, 2], "y": [10, 20, 30]}])


def legend_entries(r) -> list[str]:
    return plot_data(r)["panels"][0]["legend"]["entries"]


def test_the_right_axis_legend_entry_is_not_cut_on_a_default_chart():
    r = ok(xy, series=TWO_AXES, y2_columns=LONG, format="svg")
    assert legend_entries(r) == ["v(sw)", "v(nout).phase_deg (right axis)"]
    assert plot_data(r)["truncated_text"] == [] and not any("ellipsis" in w for w in r.warnings)
    assert "v(nout).phase_deg (right axis)" in texts(svg_text(r))


def test_a_narrow_chart_keeps_the_200_px_budget_and_a_very_long_name_is_still_cut():
    narrow = ok(xy, series=TWO_AXES, y2_columns=LONG, width=420, height=300, format="svg")
    assert legend_entries(narrow)[1].endswith("…") and plot_data(narrow)["truncated_text"]
    long_name = "x" * 120
    r = ok(xy, series=json.dumps([{"name": "a", "x": [0, 1], "y": [1, 2]}, {"name": long_name, "x": [0, 1], "y": [2, 1]}]), width=2400, format="svg")
    assert legend_entries(r)[1].endswith("…") and any("ellipsis" in w for w in r.warnings)


def test_the_budget_grows_with_the_plot_width_between_the_old_200_and_420_px():
    assert legend_text_budget(100) == LEGEND_TEXT_MAX == 200 and legend_text_budget(470) == 200
    assert 200 < legend_text_budget(600) < legend_text_budget(800) < LEGEND_TEXT_WIDEST and legend_text_budget(5000) == LEGEND_TEXT_WIDEST


def test_bar_and_panels_legends_use_the_same_budget():
    name = "Q2 revenue north region (provisional)"  # 38 characters: wider than 200 px at the 0.6 em estimate, narrower than 420
    bars = ok(bar, series=json.dumps([{"name": "plan", "x": ["a", "b"], "y": [1, 2]}, {"name": name, "x": ["a", "b"], "y": [2, 1]}]), format="svg")
    assert not plot_data(bars)["truncated_text"] and name in texts(svg_text(bars))
    two = json.dumps([{"name": "plan", "x": [0, 1], "y": [1, 2]}, {"name": name, "x": [0, 1], "y": [2, 1]}, {"name": "other", "x": [0, 1], "y": [0, 1]}])
    stacked = ok(panels, panels=f"A|plan,{name};B|other", series=two, format="svg")
    assert not plot_data(stacked)["truncated_text"] and name in texts(svg_text(stacked))


# ----------------------------------------------------------------------------------------------- hlines on bar charts (C3-5)

BARS = json.dumps([{"name": "load", "x": ["A", "B", "C"], "y": [3, 5, 4]}])


def dashed_lines(svg: str) -> list:
    from tests.plot_testing import elements

    return [el for el in elements(svg, "line") if el.get("stroke-dasharray") == "6 4"]


def test_plot_bar_draws_limit_lines_and_the_axis_grows_to_show_them():
    plain = ok(bar, series=BARS, format="svg")
    r = ok(bar, series=BARS, hlines="8,allowable;4.5,", y_label="Load (kN)", format="svg")
    assert not dashed_lines(svg_text(plain)) and plot_data(plain)["reference_lines"] == {"hlines": []}
    lines = dashed_lines(svg_text(r))
    assert len(lines) == 2
    assert plot_data(r)["axes"]["y"]["max"] >= 8 and plot_data(plain)["axes"]["y"]["max"] < 8
    assert plot_data(r)["reference_lines"] == {"hlines": [{"value": 8.0, "label": "allowable"}, {"value": 4.5, "label": ""}]}
    assert "allowable" in texts(svg_text(r))
    ya = plot_data(r)["axes"]["y"]["mapping"]
    expected = ya["origin_px"] + (8.0 - ya["min"]) * ya["scale_px_per_unit"]  # the line sits where the y mapping puts 8
    assert min(abs(float(el.get("y1")) - expected) for el in lines) < 0.01


def test_a_limit_line_outside_a_fixed_y_range_is_reported_not_drawn_and_histograms_take_it_too():
    r = ok(bar, series=BARS, y_range="0,6", hlines="8,too high;5,ok", format="svg")
    assert len(dashed_lines(svg_text(r))) == 1 and any("hline 8 is outside the y range" in w for w in r.warnings)
    values = json.dumps([{"name": "v", "y": [1, 2, 2, 3, 3, 3, 4]}])
    h = ok(bar, mode="histogram", series=values, bins=4, hlines="2,min count", format="svg")
    assert len(dashed_lines(svg_text(h))) == 1 and "min count" in texts(svg_text(h))


def test_a_bad_hlines_record_is_refused_at_its_pointer():
    fails(bar, "input.invalid", "hlines/1", series=BARS, hlines="5,ok;abc,bad")
    fails(bar, "input.out_of_range", "hlines", series=BARS, hlines=";".join(["1,x"] * 11))
