"""DWG input for drawing_read and drawing_convert, through a FAKE dwg2dxf (tests/fake_dwg2dxf.py behind a shim).

LibreDWG is not installed here and is never run: these tests check everything the toolkit does around the program (finding it,
the exact argument list, the environment and directory it gets, the limits, what it does with the program's output) and that
the DXF it returns goes through the unchanged DXF paths.  The fake copies a hand-written DXF holding one 100 mm line, so the
expected numbers come from that file:  100 mm = 283.465 pt at 1:1 in the PDF, an extent of 100 mm in drawing_read.
Whether the real dwg2dxf behaves the way the fake does is NOT tested here (see docs/drawing-dwg/design.md).
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import convert_check as CC
import dwg_fixtures as F
import pytest
import raw_dxf as R
from fastmcp import Client

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.drawing import dwg
from eng_toolkit.domains.drawing import tools as drawing_tools
from eng_toolkit import external as X
from eng_toolkit.external_catalog import DWG2DXF
from eng_toolkit.gateway.server import gateway

pytest.importorskip("ezdxf", reason="ezdxf is not installed; run: uv sync --extra drawing")
pytest.importorskip("pdfminer", reason="pdfminer.six is not installed; run: uv sync --extra drawing")

SECRETS = {"ANTHROPIC_API_KEY": "sk-secret-1", "OPENAI_API_KEY": "sk-secret-2", "AWS_SECRET_ACCESS_KEY": "secret-3", "GITHUB_TOKEN": "ghp-secret-4", "MY_PASSWORD": "secret-5"}


def read(path, **kwargs) -> ToolResult:
    fn = getattr(drawing_tools.read, "fn", drawing_tools.read)
    result = fn(path=str(path), **kwargs)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    return result


def convert(path, fmt="pdf", **kwargs) -> ToolResult:
    fn = getattr(drawing_tools.convert, "fn", drawing_tools.convert)
    result = fn(input_path=str(path), output_format=fmt, **kwargs)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    return result


def failed(result: ToolResult, family: str) -> ToolResult:
    assert result.ok is False, f"expected {family}, got ok"
    assert result.error.family == family, result.error
    assert result.warnings == [] and result.value is None and result.values == {}
    return result


def artifact(result: ToolResult, name: str) -> Path:
    return next(Path(a.path) for a in result.artifacts if Path(a.path).name == name)


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def fake(tmp_path, monkeypatch) -> F.Fake:
    return F.install_fake(tmp_path, monkeypatch)


@pytest.fixture
def a_dwg(tmp_path) -> Path:
    return F.write_dwg(tmp_path / "plate.dwg")


# --------------------------------------------------------------------------- the whole path, direct and through the gateway


def test_drawing_read_reads_a_dwg_through_the_tool(fake, a_dwg):
    r = read(a_dwg)
    assert r.ok, r.error
    assert r.values["entity_count"].quantity == 1
    assert r.values["extent_width"].quantity == pytest.approx(100.0) and r.values["extent_height"].quantity == pytest.approx(0.0)
    info = r.data["dwg"]
    assert (info["source_format"], info["dwg_version"], info["dwg_release"]) == ("dwg", "AC1032", "R2018")
    assert info["engine_version"] == "0.13.3" and info["found_via"] == "env: ENG_TOOLKIT_DWG2DXF"
    assert "GPL-3.0" in info["converted_by"] and "separate program" in info["converted_by"]
    assert r.data["file"]["name"] == "plate.dwg" and r.data["file"]["bytes"] == a_dwg.stat().st_size and r.data["file"]["source_format"] == "dwg"
    assert any("LibreDWG" in w and "R2018" in w and "0.13.3" in w for w in r.warnings)
    assert r.review_status == "unreviewed"
    full = json.loads(artifact(r, "drawing_read.json").read_text(encoding="utf-8"))
    assert full["dwg"]["dwg_version"] == "AC1032" and full["file"]["name"] == "plate.dwg"


def test_drawing_convert_makes_a_pdf_of_a_dwg_at_true_scale(fake, a_dwg):
    r = convert(a_dwg, "pdf")
    assert r.ok, r.error
    pdf = artifact(r, "converted.pdf").read_bytes()
    assert CC.pdf_media_box(pdf) == (340.16, 56.69)
    assert CC.pdf_length_pt(pdf) == pytest.approx(283.465, abs=0.01)  # 100 mm at 1:1
    assert r.values["scale"].quantity == 1.0 and r.values["mm_per_drawing_unit"].quantity == 1.0
    assert r.data["dwg"]["dwg_release"] == "R2018" and r.data["output_files"] == [str(artifact(r, "converted.pdf"))]
    assert any("LibreDWG" in w for w in r.warnings)


def test_the_converted_dxf_is_recorded_with_its_hash_and_is_the_file_the_fake_wrote(fake, a_dwg):
    r = convert(a_dwg, "svg")
    meta = r.data["dwg"]["converted_dxf"]
    assert Path(meta["path"]).is_file() and meta["sha256"] == sha(meta["path"]) == sha(fake.config["dxf"]) and meta["bytes"] == Path(meta["path"]).stat().st_size


def test_provenance_names_the_dwg_the_engine_and_the_tool_version(fake, a_dwg):
    r = read(a_dwg)
    (ref,) = r.provenance.input_files
    assert Path(ref.path).resolve() == a_dwg.resolve() and ref.sha256 == sha(a_dwg) and ref.bytes == a_dwg.stat().st_size
    assert r.provenance.engine_versions["dwg2dxf"] == "0.13.3" and "ezdxf" in r.provenance.engine_versions


async def _call(tool, args):
    async with Client(gateway) as client:
        raw = await client.call_tool(tool, args, raise_on_error=False)
    json.dumps(raw.structured_content, allow_nan=False)
    return ToolResult.model_validate(raw.structured_content)


@pytest.mark.asyncio
async def test_a_client_reads_a_dwg_through_the_gateway(fake, a_dwg):
    r = await _call("drawing_read", {"path": str(a_dwg)})
    assert r.ok, r.error
    assert r.values["entity_count"].quantity == 1 and r.values["extent_width"].quantity == pytest.approx(100.0)
    assert r.data["dwg"]["dwg_release"] == "R2018" and r.review_status == "unreviewed"


@pytest.mark.asyncio
async def test_a_client_converts_a_dwg_to_pdf_through_the_gateway(fake, a_dwg):
    r = await _call("drawing_convert", {"input_path": str(a_dwg), "output_format": "pdf"})
    assert r.ok, r.error
    assert CC.pdf_length_pt(artifact(r, "converted.pdf").read_bytes()) == pytest.approx(283.465, abs=0.01)
    assert r.data["dwg"]["engine_version"] == "0.13.3" and r.review_status == "unreviewed"


@pytest.mark.asyncio
async def test_a_client_without_the_tool_gets_a_structured_error_not_a_crash(tmp_path, monkeypatch, a_dwg):
    F.remove_tool(monkeypatch, tmp_path)
    for tool, args in (("drawing_read", {"path": str(a_dwg)}), ("drawing_convert", {"input_path": str(a_dwg), "output_format": "pdf"})):
        r = await _call(tool, args)
        assert r.ok is False and r.error.family == "engine.unavailable" and r.review_status == "unreviewed"
    r = await _call("drawing_read", {"path": str(F.known_dxf(tmp_path / "plain.dxf"))})  # the server is fine and DXF still works
    assert r.ok and r.values["entity_count"].quantity == 1


# --------------------------------------------------------------------------- what the program is given


def test_the_arguments_are_exactly_the_expected_list_and_nothing_else(fake, a_dwg):
    r = convert(a_dwg, "pdf")
    job = Path(r.data["job_dir"])
    (call,) = fake.conversions()
    assert call["argv"] == ["-o", str(job / "dwg2dxf" / "output.dxf"), str(job / "dwg2dxf" / "input.dwg")]
    assert [c["argv"] for c in fake.calls()] == [["--version"], call["argv"]]  # one version query, then the conversion; no other call


def test_it_runs_in_the_job_directory_with_the_input_it_was_given(fake, a_dwg):
    r = convert(a_dwg, "pdf")
    job = Path(r.data["job_dir"])
    (call,) = fake.conversions()
    assert Path(call["cwd"]).resolve() == job.resolve()
    assert call["input"]["sha256"] == sha(a_dwg) and call["input"]["head"] == "AC1032"  # a copy of exactly the caller's bytes
    assert call["stdin"] == ""  # standard input is the null device: end of input at once


def test_the_original_dwg_is_never_changed(fake, a_dwg):
    before, mtime = sha(a_dwg), a_dwg.stat().st_mtime_ns
    for fn in (read, convert):
        assert fn(a_dwg).ok
    assert sha(a_dwg) == before and a_dwg.stat().st_mtime_ns == mtime and sorted(p.name for p in a_dwg.parent.iterdir() if p.suffix == ".dwg") == ["plate.dwg"]


def test_the_environment_it_sees_is_scrubbed(fake, a_dwg, monkeypatch):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ENG_TOOLKIT_SOMETHING", "internal")
    r = convert(a_dwg, "pdf")
    job = Path(r.data["job_dir"])
    for call in fake.calls():  # the conversion and the version query
        seen = call["env"]
        text = json.dumps(seen)
        assert not any(value in text for value in SECRETS.values()) and "internal" not in text
        assert not any(key.upper().startswith("ENG_TOOLKIT") for key in seen)
        allowed = {
            "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "LANG", "LC_ALL", "LC_CTYPE",
            "LD_LIBRARY_PATH", "TMP", "TEMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
        }
        if sys.platform == "win32":
            allowed = allowed | {"PROMPT"}  # added by cmd.exe itself, which runs the .cmd shim
        assert {k.upper() for k in seen if not k.startswith("=")} <= allowed, sorted(seen)
    (conversion,) = fake.conversions()
    temp = Path(conversion["env"].get("TMP") or conversion["env"]["TMPDIR"])
    assert temp.parent.name.startswith("etk") and not temp.parent.exists() and not (job / "tmp").exists()  # a short scratch folder, removed afterwards


def test_a_file_name_the_c_library_could_not_open_never_reaches_the_program(fake, tmp_path):
    odd = F.write_dwg(tmp_path / "圖面 -x 設計.dwg")
    r = convert(odd, "pdf")
    assert r.ok, r.error
    (call,) = fake.conversions()
    assert [Path(a).name for a in call["argv"][1:]] == ["output.dxf", "input.dwg"] and call["argv"][0] == "-o"
    assert all(a.isascii() for a in call["argv"])
    assert Path(r.provenance.input_files[0].path).name == "圖面 -x 設計.dwg"


def test_every_supported_release_goes_through_and_is_named(fake, tmp_path):
    for release, code in F.DWG_HEADER.items():
        r = read(F.write_dwg(tmp_path / f"{release}.dwg", code))
        assert r.ok, (release, r.error)
        assert (r.data["dwg"]["dwg_release"], r.data["dwg"]["dwg_version"]) == (release, code.decode())
    assert len(fake.conversions()) == 7


# --------------------------------------------------------------------------- routing


def test_a_dwg_renamed_dxf_is_sent_to_the_tool(fake, tmp_path):
    disguised = F.write_dwg(tmp_path / "really-a-dwg.dxf")
    for fn in (read, convert):
        r = fn(disguised)
        assert r.ok, r.error
        assert r.data["dwg"]["dwg_release"] == "R2018"
    assert len(fake.conversions()) == 2


def test_a_real_dxf_is_never_sent_to_the_tool(fake, tmp_path):
    plain = F.known_dxf(tmp_path / "plain.dxf")
    binary_like = tmp_path / "binary.dxf"
    binary_like.write_bytes(b"AutoCAD Binary DXF\r\n\x1a\x00" + bytes(40))
    for fn in (read, convert):
        r = fn(plain)
        assert r.ok and "dwg" not in r.data and not any("LibreDWG" in w for w in r.warnings)
    convert(binary_like)  # whatever becomes of it, it is not a DWG
    assert fake.calls() == []  # not even a version query


def test_a_pdf_never_reaches_the_tool(fake, tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\n")
    read(p)
    assert fake.calls() == []


def test_a_dxf_named_dwg_is_refused_and_not_sent(fake, tmp_path):
    p = F.known_dxf(tmp_path / "plain.dwg")
    for fn in (read, convert):
        r = failed(fn(p), "input.invalid")
        assert "not a DWG" in r.error.message and "looks like a DXF" in r.error.message
    assert fake.calls() == []


@pytest.mark.parametrize("content", [b"", b"AC", b"hello world, not a drawing", b"\x00" * 100, b"AC1.50 something", b"MC0.0 something"])
def test_a_dwg_without_a_dwg_header_is_input_invalid(fake, tmp_path, content):
    p = tmp_path / "x.dwg"
    p.write_bytes(content)
    for fn in (read, convert):
        r = failed(fn(p), "input.invalid")
        assert "DWG header" in r.error.message
    assert fake.calls() == []


@pytest.mark.parametrize(("code", "word"), [(b"AC1006", "R10"), (b"AC1012", "R13"), (b"AC1014", "R14"), (b"AC1002", "unknown release"), (b"AC1033", "unknown release"), (b"AC1099", "unknown release")])
def test_an_older_or_unknown_release_is_refused_clearly_and_not_sent(fake, tmp_path, code, word):
    p = F.write_dwg(tmp_path / "old.dwg", code)
    for fn in (read, convert):
        r = failed(fn(p), "input.unsupported_format")
        assert word in r.error.message and code.decode() in r.error.message and "R2018" in r.error.message and "old.dwg" in r.error.message
    assert fake.calls() == []


# --------------------------------------------------------------------------- the program is missing


def test_without_the_program_the_error_says_how_to_get_it_and_nothing_was_opened(tmp_path, monkeypatch, a_dwg):
    F.remove_tool(monkeypatch, tmp_path)
    for fn in (read, convert):
        r = failed(fn(a_dwg), "engine.unavailable")
        m = r.error.message
        assert "dwg2dxf" in m and "ENG_TOOLKIT_DWG2DXF" in m and "https://www.gnu.org/software/libredwg/" in m and "PATH" in m
        assert not any(bad in m.lower() for bad in ("curl", "wget", "pip install", "sudo", "irm ", "iex"))
        assert r.error.details["tool"] == "dwg2dxf" and r.error.details["env_var"] == "ENG_TOOLKIT_DWG2DXF" and r.error.details["path"] == str(a_dwg)
        assert r.provenance.input_files == []  # the DWG was not even opened


def test_a_variable_that_names_nothing_is_engine_unavailable(tmp_path, monkeypatch, a_dwg):
    monkeypatch.setenv("ENG_TOOLKIT_DWG2DXF", str(tmp_path / "does-not-exist.exe"))
    r = failed(read(a_dwg), "engine.unavailable")
    assert "does-not-exist.exe" in r.error.message and "not a file" in r.error.message
    monkeypatch.setenv("ENG_TOOLKIT_DWG2DXF", "dwg2dxf")  # not a full path
    assert "not a full path" in failed(convert(a_dwg), "engine.unavailable").error.message


def test_a_program_named_dwg2dxf_on_path_is_found_but_never_one_in_the_working_directory(tmp_path, monkeypatch, a_dwg):
    F.remove_tool(monkeypatch, tmp_path)
    here = tmp_path / "cwd"
    here.mkdir()
    (here / "dwg2dxf.exe").write_bytes(b"MZ")
    (here / "dwg2dxf").write_text("#!/bin/sh\n")
    monkeypatch.chdir(here)
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", str(tmp_path / "empty-bin")]))
    failed(read(a_dwg), "engine.unavailable")
    found, _ = X.locate(DWG2DXF)
    assert found is None


def test_a_dxf_still_works_without_the_program(tmp_path, monkeypatch):
    F.remove_tool(monkeypatch, tmp_path)
    p = F.known_dxf(tmp_path / "plain.dxf")
    assert read(p).ok and convert(p).ok


def test_a_directory_outside_the_read_list_is_refused_before_anything_runs(fake, tmp_path, monkeypatch, a_dwg):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(allowed))
    for fn in (read, convert):
        failed(fn(a_dwg), "input.path_not_allowed")
    assert fake.conversions() == []


# --------------------------------------------------------------------------- the program fails


def test_a_non_zero_exit_is_engine_failure_with_the_end_of_its_stderr(tmp_path, monkeypatch, a_dwg):
    fake = F.install_fake(tmp_path, monkeypatch, mode="fail", stderr="ERROR: Invalid DWG section checksum", exit=1)
    for fn in (read, convert):
        r = failed(fn(a_dwg), "engine.failure")
        assert "exited with code 1" in r.error.message and "Invalid DWG section checksum" in r.error.message and "plate.dwg" in r.error.message
        assert r.error.details["returncode"] == 1 and r.error.details["stderr_tail"] == "ERROR: Invalid DWG section checksum"
    assert len(fake.conversions()) == 2


def test_the_stderr_is_cut_to_a_few_hundred_characters_and_has_no_job_directory_path(tmp_path, monkeypatch, a_dwg):
    fake = F.install_fake(tmp_path, monkeypatch, mode="fail", stderr="x" * 5000 + " END", exit=2, echo_args=True)
    r = failed(convert(a_dwg), "engine.failure")
    job = r.data["job_dir"]
    text = json.dumps(r.model_dump(mode="json")["error"])
    assert r.error.details["returncode"] == 2 and len(r.error.details["stderr_tail"]) <= 303
    assert len(r.error.message) < 700
    assert str(job) not in text and str(job).replace("\\", "\\\\") not in text and str(job).replace("\\", "/") not in text
    assert "<job>" in r.error.details["stderr_tail"] and "END" not in r.error.details["stderr_tail"] or "<job>" in r.error.message
    assert fake.conversions()


def test_exit_zero_without_an_output_file_is_engine_failure(tmp_path, monkeypatch, a_dwg):
    F.install_fake(tmp_path, monkeypatch, mode="nothing")
    r = failed(read(a_dwg), "engine.failure")
    assert "no output file" in r.error.message


@pytest.mark.parametrize(("mode", "needle"), [("garbage", "not a DXF"), ("empty", "empty file"), ("notdxf_big", "not a DXF")])
def test_output_that_is_not_a_dxf_is_drawing_corrupt(tmp_path, monkeypatch, a_dwg, mode, needle):
    F.install_fake(tmp_path, monkeypatch, mode=mode)
    for fn in (read, convert):
        r = failed(fn(a_dwg), "drawing.corrupt")
        assert needle in r.error.message and "plate.dwg" in r.error.message


def test_a_program_that_takes_too_long_is_stopped_and_its_process_is_gone(tmp_path, monkeypatch, a_dwg):
    monkeypatch.setenv("ENG_TOOLKIT_DWG2DXF_TIMEOUT_S", "2")
    fake = F.install_fake(tmp_path, monkeypatch, mode="sleep", sleep_s=40)
    started = time.monotonic()
    r = failed(read(a_dwg), "engine.timeout")
    assert time.monotonic() - started < 25
    assert "dwg2dxf" in r.error.message and "2 s" in r.error.message and "ENG_TOOLKIT_DWG2DXF_TIMEOUT_S" in r.error.message
    assert r.error.details["timeout_s"] == 2.0
    (call,) = fake.conversions()
    deadline = time.monotonic() + 10
    while F.pid_alive(call["pid"]) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not F.pid_alive(call["pid"])


def test_a_huge_output_is_stopped_while_it_is_written(tmp_path, monkeypatch, a_dwg):
    monkeypatch.setenv("ENG_TOOLKIT_READ_MAX_MB", "1")
    monkeypatch.setenv("ENG_TOOLKIT_CONVERT_MAX_MB", "1")
    fake = F.install_fake(tmp_path, monkeypatch, mode="huge", huge_bytes=80_000_000)
    for fn in (read, convert):
        r = failed(fn(a_dwg), "drawing.too_large")
        assert r.error.details["limit_bytes"] == 1_000_000 and "1000000" in r.error.message
        output = Path(r.data["job_dir"]) / "dwg2dxf" / "output.dxf"
        assert output.stat().st_size < 20_000_000  # stopped, not left to write 80 MB
    for call in fake.conversions():
        deadline = time.monotonic() + 10
        while F.pid_alive(call["pid"]) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not F.pid_alive(call["pid"])


def test_the_default_output_cap_is_the_dxf_size_limit(fake, a_dwg):
    r = read(a_dwg)
    assert r.data["dwg"]["limits"]["max_output_bytes"] == 50_000_000 == r.data["limits"]["max_file_bytes"]
    assert r.data["dwg"]["limits"]["timeout_s"] == 60.0 and r.data["dwg"]["limits"]["memory_mb"] == 2048


def test_a_program_in_a_known_location_is_found_when_nothing_else_names_it(fake, a_dwg, tmp_path, monkeypatch):
    from dataclasses import replace

    name = "dwg2dxf.exe" if sys.platform == "win32" else "dwg2dxf"
    place = tmp_path / "apps" / "libredwg-0.13" / "bin"
    place.mkdir(parents=True)
    shim = place / ("dwg2dxf.cmd" if sys.platform == "win32" else "dwg2dxf")
    shim.write_bytes(fake.shim.read_bytes())
    shim.chmod(0o755)
    if sys.platform == "win32":  # a .cmd is started only through the variable; a known location needs a real .exe, so check the finding, not a run
        (place / name).write_bytes(b"MZ")
    monkeypatch.delenv("ENG_TOOLKIT_DWG2DXF")
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    monkeypatch.setattr(dwg, "TOOL", replace(dwg.TOOL, locations=(str(tmp_path / "apps" / "libredwg-*" / "bin"),)))
    found = dwg.require_tool()
    assert found.source == "location" and found.path == str(place / name)


def test_the_operator_can_change_the_time_and_memory_limits(fake, a_dwg, monkeypatch):
    monkeypatch.setenv("ENG_TOOLKIT_DWG2DXF_TIMEOUT_S", "17.5")
    monkeypatch.setenv("ENG_TOOLKIT_DWG2DXF_MEMORY_MB", "700")
    assert dwg.limits() == (17.5, 700)
    r = read(a_dwg)
    assert r.data["dwg"]["limits"]["timeout_s"] == 17.5 and r.data["dwg"]["limits"]["memory_mb"] == 700
    for bad in ("", "abc", "-5", "0", "inf", "nan"):
        monkeypatch.setenv("ENG_TOOLKIT_DWG2DXF_TIMEOUT_S", bad)
        assert dwg.limits()[0] == 60.0


@pytest.mark.skipif(sys.platform != "win32" and not sys.platform.startswith("linux"), reason="no memory cap on this platform")
def test_the_memory_cap_was_applied_to_the_program(fake, a_dwg):
    assert read(a_dwg).data["dwg"]["limits"]["memory_cap_applied"] is True


# --------------------------------------------------------------------------- the version


def test_the_tool_version_is_unknown_when_it_prints_nothing_or_fails(tmp_path, monkeypatch, a_dwg):
    fake = F.install_fake(tmp_path, monkeypatch, version="")
    r = read(a_dwg)
    assert r.ok and r.data["dwg"]["engine_version"] == "unknown" and r.provenance.engine_versions["dwg2dxf"] == "unknown"
    assert any("version unknown" in w for w in r.warnings)
    fake.set(mode="version_broken")  # --version exits 3; the conversion itself then fails in this mode, so use copy with a broken query
    fake.set(mode="copy", version="")
    assert convert(a_dwg).data["dwg"]["engine_version"] == "unknown"


def test_the_version_is_the_first_line_it_prints_for_dash_dash_version(tmp_path, monkeypatch, a_dwg):
    F.install_fake(tmp_path, monkeypatch, version="dwg2dxf 0.12.5.6012\nCopyright (C) Free Software Foundation")
    assert convert(a_dwg).data["dwg"]["engine_version"] == "0.12.5.6012"


# --------------------------------------------------------------------------- the DXF it returns is held to the DXF rules


def test_a_hostile_dxf_from_the_program_meets_the_same_refusals_as_a_dxf_file(tmp_path, monkeypatch, a_dwg):
    hostile = tmp_path / "hostile.dxf"
    R.write(hostile, R.document(R.V2000, insunits=4, entities=(R.line((0, 0), (1e200, 5)),)))
    plain = convert(hostile)
    F.install_fake(tmp_path, monkeypatch, dxf=str(hostile))
    via_dwg = convert(a_dwg)
    assert plain.ok is False and via_dwg.ok is False
    assert via_dwg.error.family == plain.error.family == "input.out_of_range"


def test_a_dxf_with_too_many_records_from_the_program_is_too_large_like_a_file(tmp_path, monkeypatch, a_dwg):
    big = tmp_path / "many.dxf"
    R.write(big, R.document(R.V2000, insunits=4, entities=tuple(R.line((i, 0), (i, 1)) for i in range(400))))
    monkeypatch.setenv("ENG_TOOLKIT_CONVERT_MAX_MB", "0.0001")  # a 100-byte file limit: the DWG is larger than that too
    plain = failed(convert(big), "drawing.too_large")
    assert plain.error.family == "drawing.too_large"
    monkeypatch.delenv("ENG_TOOLKIT_CONVERT_MAX_MB")
    F.install_fake(tmp_path, monkeypatch, dxf=str(big))
    assert convert(a_dwg).ok  # within the limits, a long DXF converts


def test_a_dxf_written_in_binary_form_is_accepted_as_a_dxf_start():
    assert dwg.looks_like_dxf(b"AutoCAD Binary DXF\r\n\x1a\x00")
    assert dwg.looks_like_dxf(b"  0\r\nSECTION\r\n  2\r\nHEADER\r\n")
    assert dwg.looks_like_dxf(b"999\nwritten by LibreDWG\n  0\nSECTION\n  2\nHEADER\n")
    assert dwg.looks_like_dxf(b"\xef\xbb\xbf0\nSECTION\n")
    for bad in (b"", b"\x7fELF", b"AC1032\x00\x00", b"0\nENDSEC\n", b"<html>", b"hello\n0\nSECTION\n"):
        assert not dwg.looks_like_dxf(bad)


# --------------------------------------------------------------------------- the doctor and the descriptions


@pytest.mark.asyncio
async def test_the_doctor_shows_a_dwg2dxf_row_found_or_not_without_failing(tmp_path, monkeypatch):
    F.remove_tool(monkeypatch, tmp_path)
    r = await _call("toolkit_doctor", {"scan_env": False})
    (row,) = [t for t in r.data["external_tools"] if t["name"] == "dwg2dxf"]
    assert row["found"] is False and row["status"] == "not-installed (optional)" and row["declared_tier"] == "gpl-isolated" and row["version"] is None
    assert not any("dwg2dxf" in f for f in r.data["failures"])
    fake = F.install_fake(tmp_path, monkeypatch)
    r = await _call("toolkit_doctor", {"scan_env": False})
    (row,) = [t for t in r.data["external_tools"] if t["name"] == "dwg2dxf"]
    assert row["found"] is True and row["path"] == str(fake.shim) and row["version"] == "0.13.3" and row["status"] == "ok"
    assert not any("dwg2dxf" in f for f in r.data["failures"])


def test_the_descriptions_tell_a_model_what_a_dwg_needs():
    for text in (drawing_tools.READ_DESCRIPTION, drawing_tools.CONVERT_DESCRIPTION):
        for needle in ("DWG", "dwg2dxf", "ENG_TOOLKIT_DWG2DXF", "R2018", "engine.unavailable", "LibreDWG", "data.dwg", "separate program"):
            assert needle in text, needle
        assert "S9" not in text and "DWG is not read" not in text


# --------------------------------------------------------------------------- the real program, only if it is there


REAL = X.locate(DWG2DXF)[0]
SAMPLE = os.environ.get("ENG_TOOLKIT_TEST_DWG", "")


@pytest.mark.skipif(REAL is None or not SAMPLE or not Path(SAMPLE).is_file(), reason="needs a real LibreDWG dwg2dxf (ENG_TOOLKIT_DWG2DXF or PATH) and a sample DWG (ENG_TOOLKIT_TEST_DWG)")
def test_the_real_dwg2dxf_converts_a_sample_drawing():
    r = read(SAMPLE)
    assert r.ok, r.error
    assert r.data["dwg"]["engine_version"] != "unknown" and r.values["entity_count"].quantity > 0
