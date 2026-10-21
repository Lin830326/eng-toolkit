"""The dsp domain through a real MCP client against the gateway: registry, schemas, strict JSON and hostile inputs."""

import json
import math
import re
from pathlib import Path

import pytest
from fastmcp import Client

import eng_toolkit.domains.dsp as dsp_package
from eng_toolkit.contract import ToolResult
from eng_toolkit.domains import all_domains
from eng_toolkit.gateway.server import gateway
from tests.dsp_testing import assert_finite

DSP = {"dsp_filter_design", "dsp_spectrum", "dsp_filter_apply", "dsp_sampling", "dsp_correlate", "dsp_window"}
SINE = json.dumps([math.sin(2 * math.pi * 5 * k / 64) for k in range(64)])

GOOD = {
    "dsp_filter_design": {"family": "butterworth", "response": "lowpass", "sample_rate": "8 kHz", "order": 4, "cutoff": "1 kHz"},
    "dsp_spectrum": {"samples": SINE, "sample_rate": "64 Hz", "window": "hann", "adc_metrics": True},
    "dsp_filter_apply": {"samples": SINE, "sample_rate": "64 Hz", "family": "butterworth", "response": "lowpass", "order": 2, "cutoff": "10 Hz"},
    "dsp_sampling": {"operation": "alias", "frequency": "1.3 kHz", "sample_rate": "1 kHz"},
    "dsp_correlate": {"samples": SINE, "sample_rate": "64 Hz", "envelope": True},
    "dsp_window": {"window": "hann", "length": 256},
}


def _no_constants(name: str):
    raise AssertionError(f"the response contains the JSON constant {name}")


async def _call(client: Client, tool: str, args: dict, *, expect_ok: bool | None = True):
    result = await client.call_tool(tool, args, raise_on_error=False)
    if result.is_error:
        assert expect_ok is not True, (tool, args, result.content)
        return None  # refused by the schema layer before the tool ran
    for block in result.content:  # the raw text must be strict JSON: no NaN / Infinity
        json.loads(block.text, parse_constant=_no_constants)
    parsed = ToolResult.model_validate(result.structured_content)
    assert_finite(result.structured_content)
    assert parsed.review_status == "unreviewed" and parsed.provenance.engine == "eng_toolkit.dsp"
    if expect_ok is True:
        assert parsed.ok, parsed.error
    return parsed


def test_registry_has_six_dsp_tools_and_only_numpy_as_engine():
    domains = {d.name: d for d in all_domains()}
    assert set(domains["dsp"].tools) == {"filter_design", "spectrum", "filter_apply", "sampling", "correlate", "window"}
    assert len(domains["dsp"].tools) <= 6 and domains["dsp"].engines == ("numpy",)


@pytest.mark.asyncio
async def test_tools_are_listed_with_basic_types_and_compact_descriptions():
    async with Client(gateway) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert DSP <= set(tools)
    for name in DSP:
        assert 40 < len(tools[name].description) < 1500, name
        for pname, prop in tools[name].input_schema.get("properties", {}).items():
            assert prop.get("type") in {"string", "number", "integer", "boolean"}, (name, pname, prop)
            assert "anyOf" not in prop and "oneOf" not in prop
            assert prop.get("description"), (name, pname)


@pytest.mark.asyncio
async def test_toolkit_lists_the_dsp_domain():
    async with Client(gateway) as client:
        r = await client.call_tool("toolkit_list_tools", {})
    assert all(name in json.dumps(r.structured_content) for name in DSP)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", sorted(GOOD))
async def test_every_tool_answers_through_the_client_with_strict_json(tool):
    async with Client(gateway) as client:
        parsed = await _call(client, tool, GOOD[tool])
    assert parsed.ok and parsed.value is not None and parsed.values
    assert parsed.warnings and "Digital signal processing" in parsed.warnings[0]


@pytest.mark.asyncio
async def test_csv_artifacts_are_written_and_hashed():
    async with Client(gateway) as client:
        got = {t: await _call(client, t, GOOD[t]) for t in ("dsp_filter_design", "dsp_spectrum", "dsp_filter_apply", "dsp_correlate")}
    for tool, name, header in (
        ("dsp_filter_design", "filter_response.csv", "frequency_hz,magnitude_db,phase_deg"),
        ("dsp_spectrum", "spectrum.csv", "frequency_hz,amplitude,psd_unit2_per_hz"),
        ("dsp_filter_apply", "filtered.csv", "index,time_s,input,output"),
        ("dsp_correlate", "envelope.csv", "index,time_s,x,envelope"),
    ):
        art = next(a for a in got[tool].artifacts if a.path.endswith(name))
        assert art.format == "csv" and len(art.sha256) == 64
        lines = Path(art.path).read_text(encoding="utf-8").splitlines()
        assert lines[0] == header and len(lines) > 30 and all(math.isfinite(float(x)) for x in lines[-1].split(","))


BASE = {"family": "butterworth", "response": "lowpass", "sample_rate": "8 kHz", "order": 4, "cutoff": "1 kHz"}
SAMPLES = "[0, 1, 0, -1, 0, 1, 0, -1, 0, 1, 0, -1]"

HOSTILE = [
    # (tool, arguments, expected error family)
    ("dsp_filter_design", {**BASE, "cutoff": "0 Hz"}, "input.invalid"),               # cutoff at 0
    ("dsp_filter_design", {**BASE, "cutoff": "4 kHz"}, "input.invalid"),              # exactly Nyquist
    ("dsp_filter_design", {**BASE, "cutoff": "9 kHz"}, "input.invalid"),              # above Nyquist
    ("dsp_filter_design", {**BASE, "order": 41}, "input.out_of_range"),
    ("dsp_filter_design", {**BASE, "order": 10 ** 12}, "input.out_of_range"),
    ("dsp_filter_design", {**BASE, "response": "bandpass", "cutoff": "2 kHz, 1 kHz"}, "input.invalid"),
    ("dsp_filter_design", {"family": "butterworth", "response": "lowpass", "sample_rate": "8 kHz", "passband_edge": "2 kHz", "stopband_edge": "1 kHz", "passband_ripple_db": 1.0, "stopband_attenuation_db": 40.0}, "input.invalid"),  # stopband edge inside the passband
    ("dsp_filter_design", {"family": "fir_hann", "response": "lowpass", "sample_rate": "8 kHz", "order": 4096, "cutoff": "1 kHz"}, "input.out_of_range"),
    ("dsp_filter_design", {**BASE, "sample_rate": "-8 kHz"}, "input.invalid"),
    ("dsp_filter_design", {**BASE, "sample_rate": "8 m"}, "units.dimension_mismatch"),
    ("dsp_filter_design", {**BASE, "sample_rate": "8000"}, "units.bare_number"),
    ("dsp_filter_design", {**BASE, "sample_rate": "9" * 5000 + " Hz"}, "input.out_of_range"),
    ("dsp_filter_design", {**BASE, "cutoff": "__import__('os').system('echo pwned') Hz"}, "units.parse_error"),
    ("dsp_filter_design", {**BASE, "family": "__import__('os')"}, "input.invalid"),
    ("dsp_spectrum", {"samples": "[" + "0," * 5_000_000 + "0]", "sample_rate": "1 kHz"}, "input.out_of_range"),   # 5 million samples
    ("dsp_spectrum", {"samples": "[1, 2, NaN, 4, 5, 6, 7, 8, 9]", "sample_rate": "1 kHz"}, "input.invalid"),
    ("dsp_spectrum", {"samples": "[1, 2, Infinity, 4, 5, 6, 7, 8, 9]", "sample_rate": "1 kHz"}, "input.invalid"),
    ("dsp_spectrum", {"samples": "[1, 2, -Infinity, 4, 5, 6, 7, 8, 9]", "sample_rate": "1 kHz"}, "input.invalid"),
    ("dsp_spectrum", {"samples": "[1, 2, 1e999, 4, 5, 6, 7, 8, 9]", "sample_rate": "1 kHz"}, "input.invalid"),
    ("dsp_spectrum", {"samples": SAMPLES, "sample_rate": "1 kHz", "window": "kaiser7"}, "input.invalid"),
    ("dsp_spectrum", {"samples": SAMPLES, "sample_rate": "1 kHz", "overlap_percent": 100.0}, "input.invalid"),
    ("dsp_spectrum", {"samples": SAMPLES, "sample_rate": "-1 kHz"}, "input.invalid"),
    ("dsp_spectrum", {"samples": SAMPLES, "sample_rate": "1 s"}, "units.dimension_mismatch"),
    ("dsp_spectrum", {"samples": SAMPLES, "sample_rate": "1 kHz", "segment_length": 10 ** 9}, "input.invalid"),
    ("dsp_spectrum", {"samples": "__import__('os').system('echo pwned')", "sample_rate": "1 kHz"}, "input.invalid"),
    ("dsp_spectrum", {"samples": "[" * 4000 + "]" * 4000, "sample_rate": "1 kHz"}, "input.invalid"),
    ("dsp_spectrum", {"samples": "x" * 101_000_000, "sample_rate": "1 kHz"}, "input.out_of_range"),
    ("dsp_filter_apply", {"samples": SAMPLES, "b": "[1]", "a": "[1, -1.5]"}, "input.invalid"),   # unstable
    ("dsp_filter_apply", {"samples": SAMPLES, "sos": "[[1, 0, 0, 1, -1.2, 0.5], [1, 0, 0, 1, 0, 1.5]]"}, "input.invalid"),
    ("dsp_filter_apply", {"samples": SAMPLES, "b": "[1, NaN]"}, "input.invalid"),
    ("dsp_filter_apply", {"samples": SAMPLES, "b": "[1e308]"}, "input.out_of_range"),
    ("dsp_filter_apply", {"samples": SAMPLES, "sos": "[[1, 0, 0, 1, 0]]"}, "input.invalid"),
    ("dsp_filter_apply", {"samples": SAMPLES, "family": "butterworth", "response": "lowpass", "sample_rate": "-8 kHz", "order": 2, "cutoff": "1 kHz"}, "input.invalid"),
    ("dsp_sampling", {"operation": "alias", "frequency": "1 kHz", "sample_rate": "1 m"}, "units.dimension_mismatch"),
    ("dsp_sampling", {"operation": "alias", "frequency": "1 kHz", "sample_rate": "-1 kHz"}, "input.invalid"),
    ("dsp_sampling", {"operation": "alias", "frequency": "1e300 Hz", "sample_rate": "1 kHz"}, "input.out_of_range"),
    ("dsp_sampling", {"operation": "quantization", "bits": 10 ** 9}, "input.out_of_range"),
    ("dsp_sampling", {"operation": "decimate", "samples": SAMPLES, "sample_rate": "1 kHz", "factor": 10 ** 9}, "input.out_of_range"),
    ("dsp_sampling", {"operation": "interpolate", "samples": "[" + "0," * 200_000 + "0]", "sample_rate": "1 kHz", "factor": 100}, "input.out_of_range"),
    ("dsp_correlate", {"samples": "[1, 2, NaN]"}, "input.invalid"),
    ("dsp_correlate", {"samples": SAMPLES, "max_lag": 10 ** 9}, "input.out_of_range"),
    ("dsp_correlate", {"samples": SAMPLES, "samples_b": "[1, 1, 1, 1]"}, "input.invalid"),
    ("dsp_window", {"window": "hann", "length": 10 ** 9}, "input.out_of_range"),
    ("dsp_window", {"window": "nuttall"}, "input.invalid"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, args, family", HOSTILE, ids=[f"{t}-{i}" for i, (t, _, _) in enumerate(HOSTILE)])
async def test_hostile_inputs_are_refused_cleanly(tool, args, family):
    async with Client(gateway) as client:
        parsed = await _call(client, tool, args, expect_ok=False)
    assert parsed is not None, "the call was rejected by the schema layer; it should reach the tool and be refused there"
    assert parsed.ok is False and parsed.value is None and parsed.error is not None
    assert parsed.error.family == family, (tool, parsed.error)
    assert len(parsed.error.message) < 1500  # a huge input is never echoed back
    assert parsed.warnings == []


@pytest.mark.asyncio
async def test_files_through_the_client_text_line_and_path_outside_the_allowed_directories(tmp_path, monkeypatch):
    allowed = tmp_path / "data"
    allowed.mkdir()
    good = allowed / "good.csv"
    good.write_text("\n".join(f"{math.sin(2 * math.pi * 5 * k / 64)!r}" for k in range(64)), encoding="utf-8")
    texty = allowed / "texty.csv"
    texty.write_text("1\n2\nthree\n4\n5\n6\n7\n8\n9\n", encoding="utf-8")
    outside = tmp_path / "outside.csv"
    outside.write_text(good.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(allowed))
    async with Client(gateway) as client:
        r = await _call(client, "dsp_spectrum", {"samples_file": str(good), "sample_rate": "64 Hz"})
        assert r.provenance.input_files[0].path.endswith("good.csv")
        r = await _call(client, "dsp_spectrum", {"samples_file": str(texty), "sample_rate": "64 Hz"}, expect_ok=False)
        assert r.error.family == "input.invalid" and r.error.details["line"] == 3 and "line 3" in r.error.message
        r = await _call(client, "dsp_spectrum", {"samples_file": str(outside), "sample_rate": "64 Hz"}, expect_ok=False)
        assert r.error.family == "input.path_not_allowed" and str(outside) not in json.dumps(r.error.details)
        r = await _call(client, "dsp_spectrum", {"samples_file": str(allowed / ".." / "outside.csv"), "sample_rate": "64 Hz"}, expect_ok=False)
        assert r.error.family == "input.path_not_allowed"
        r = await _call(client, "dsp_correlate", {"samples": SAMPLES, "samples_b_file": str(outside)}, expect_ok=False)
        assert r.error.family == "input.path_not_allowed"
        r = await _call(client, "dsp_filter_apply", {"samples_file": str(outside), "b": "[1]"}, expect_ok=False)
        assert r.error.family == "input.path_not_allowed"


@pytest.mark.asyncio
async def test_non_finite_numbers_in_number_parameters_never_succeed():
    async with Client(gateway) as client:
        for bad in (float("nan"), float("inf"), -float("inf")):
            for tool, key in (("dsp_filter_design", "passband_ripple_db"), ("dsp_spectrum", "overlap_percent"), ("dsp_window", "kaiser_beta"), ("dsp_sampling", "attenuation_db"), ("dsp_filter_apply", "stopband_attenuation_db")):
                try:
                    result = await client.call_tool(tool, {**GOOD[tool], key: bad}, raise_on_error=False)
                except Exception:  # the transport itself may refuse a non-JSON number
                    continue
                if result.is_error:
                    continue
                parsed = ToolResult.model_validate(result.structured_content)
                assert parsed.ok is False, (tool, key, bad)
                assert_finite(result.structured_content)


@pytest.mark.asyncio
async def test_absurd_but_legal_magnitudes_never_leak_infinity():
    async with Client(gateway) as client:
        for samples in ("[1e99, -1e99, 1e99, -1e99, 1e99, -1e99, 1e99, -1e99, 1e99]", "[1e-300, 0, 1e-300, 0, 1e-300, 0, 1e-300, 0, 1e-300]"):
            for tool, extra in (("dsp_spectrum", {"sample_rate": "1 kHz", "adc_metrics": True}), ("dsp_correlate", {"envelope": True}), ("dsp_filter_apply", {"b": "[1e12, -1e12]"})):
                parsed = await _call(client, tool, {"samples": samples, **extra}, expect_ok=None)
                assert parsed is None or parsed.ok or parsed.error.family.startswith("input.")


SOURCE_DIR = Path(dsp_package.__file__).parent


def test_the_package_never_imports_scipy_or_executes_text():
    forbidden = [r"\bimport\s+scipy\b", r"\bfrom\s+scipy\b", r"\beval\s*\(", r"\bexec\s*\(", r"(?<!re\.)\bcompile\s*\(",r"__import__", r"importlib", r"\bsubprocess\b", r"\bos\.system\b", r"\bpickle\b", r"\bopen\s*\("]
    for path in SOURCE_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert not re.search(pattern, text), (path.name, pattern)
