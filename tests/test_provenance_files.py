"""Provenance.input_files and Provenance.engine_versions: what a call read and with which engines."""

import hashlib
import importlib.metadata as md
import json
from pathlib import Path

import pytest

from eng_toolkit.contract import FileRef, Provenance, QuantityOut, ToolResult
from eng_toolkit.errors import ToolFailure
from eng_toolkit.runner import Computed, JobHandle, run_tool

ONE = QuantityOut(quantity=1.0, unit="dimensionless")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(compute, **kwargs) -> ToolResult:
    result = run_tool(tool_name="t_probe", engine="test.engine", version="0", inputs={"a": 1}, compute=compute, **kwargs)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    return result


def ok(job: JobHandle) -> Computed:
    return Computed(values={"x": ONE}, primary="x")


# --------------------------------------------------------------------------- the contract


def test_provenance_fields_are_optional_and_default_to_empty():
    p = Provenance(engine="e", version="1", inputs_hash="sha256:00")
    assert p.input_files == [] and p.engine_versions == {}
    old = {"engine": "e", "version": "1", "inputs_hash": "sha256:00"}
    assert Provenance.model_validate(old) == p


def test_a_file_ref_round_trips_through_json():
    p = Provenance(
        engine="e",
        version="1",
        inputs_hash="sha256:00",
        input_files=[FileRef(path="/x/a.step", bytes=12, sha256="ab" * 32)],
        engine_versions={"numpy": "2.0"},
    )
    assert Provenance.model_validate(json.loads(p.model_dump_json())) == p
    assert set(p.model_dump()["input_files"][0]) == {"path", "bytes", "sha256"}


def test_a_tool_that_reads_no_file_has_empty_lists():
    from eng_toolkit.domains.metrology.tools import fit_limits

    r = getattr(fit_limits, "fn", fit_limits)(nominal="25 mm", fit="H7/g6")
    assert r.ok
    assert r.provenance.input_files == [] and r.provenance.engine_versions == {}
    assert ToolResult.model_validate(json.loads(r.model_dump_json())) == r


# --------------------------------------------------------------------------- JobHandle.input_file


def test_input_file_records_path_size_and_hash(tmp_path):
    data = tmp_path / "data.txt"
    data.write_bytes(b"hello provenance")
    seen = []

    def compute(job):
        seen.append(job.input_file(str(data), param="path", extensions=(".txt",)))
        return ok(job)

    r = run(compute)
    assert r.ok
    # the tool gets a copy of exactly the bytes that were hashed, in the job directory, under the file's own name
    (copy,) = seen
    assert copy != data and copy.name.endswith("data.txt") and copy.suffix == ".txt" and copy.parent.name == "input"
    assert copy.read_bytes() == b"hello provenance"
    (ref,) = r.provenance.input_files
    assert ref.path == str(data.resolve())
    assert ref.bytes == len(b"hello provenance")
    assert ref.sha256 == sha256(data)


def test_a_relative_path_is_recorded_absolute(tmp_path, monkeypatch):
    (tmp_path / "rel.txt").write_text("x")
    monkeypatch.chdir(tmp_path)
    r = run(lambda job: (job.input_file("rel.txt", param="path", extensions=(".txt",)), ok(job))[1])
    assert Path(r.provenance.input_files[0].path).is_absolute()
    assert r.provenance.input_files[0].path == str((tmp_path / "rel.txt").resolve())


def test_the_extension_check_ignores_case_and_files_are_listed_in_read_order(tmp_path):
    first, second = tmp_path / "A.TXT", tmp_path / "b.csv"
    first.write_text("1")
    second.write_text("22")

    def compute(job):
        job.input_file(str(first), param="first", extensions=(".txt",))
        job.input_file(str(second), param="second", extensions=(".csv",))
        return ok(job)

    r = run(compute)
    assert [ref.bytes for ref in r.provenance.input_files] == [1, 2]
    assert [Path(ref.path).name for ref in r.provenance.input_files] == ["A.TXT", "b.csv"]


def test_a_missing_file_is_input_file_not_found_and_lists_nothing(tmp_path):
    r = run(lambda job: job.input_file(str(tmp_path / "gone.txt"), param="model_path", extensions=(".txt",)))
    assert r.ok is False
    assert r.error.family == "input.file_not_found"
    assert r.error.details["param"] == "/model_path" and "gone.txt" in r.error.details["path"]
    assert r.provenance.input_files == []


def test_a_directory_is_not_a_file(tmp_path):
    r = run(lambda job: job.input_file(str(tmp_path), param="p", extensions=(".txt",)))
    assert r.error.family == "input.file_not_found"


def test_an_unsupported_extension_is_reported_and_lists_nothing(tmp_path):
    other = tmp_path / "data.bin"
    other.write_text("x")
    r = run(lambda job: job.input_file(str(other), param="model_path", extensions=(".txt", ".csv")))
    assert r.ok is False
    assert r.error.family == "input.unsupported_format"
    assert r.error.details == {"param": "/model_path", "path": str(other), "supported": [".txt", ".csv"]}
    assert "'.bin'" in r.error.message and "model_path" in r.error.message
    assert r.provenance.input_files == []


def test_a_file_without_an_extension_says_so(tmp_path):
    bare = tmp_path / "README"
    bare.write_text("x")
    r = run(lambda job: job.input_file(str(bare), param="p", extensions=(".txt",)))
    assert "(no extension)" in r.error.message


def test_a_failed_call_keeps_the_files_it_had_already_read(tmp_path):
    good, bad = tmp_path / "good.txt", tmp_path / "bad.bin"
    good.write_text("good")
    bad.write_text("bad")

    def compute(job):
        job.input_file(str(good), param="a", extensions=(".txt",))
        job.input_file(str(bad), param="b", extensions=(".txt",))
        return ok(job)

    r = run(compute)
    assert r.error.family == "input.unsupported_format"
    assert [ref.sha256 for ref in r.provenance.input_files] == [sha256(good)]


@pytest.mark.parametrize("failure", [ToolFailure("input.invalid", "boom"), RuntimeError("engine blew up")])
def test_a_later_failure_keeps_the_files_it_had_already_read(tmp_path, failure):
    data = tmp_path / "data.txt"
    data.write_text("data")

    def compute(job):
        job.input_file(str(data), param="a", extensions=(".txt",))
        raise failure

    r = run(compute)
    assert r.ok is False
    assert [ref.path for ref in r.provenance.input_files] == [str(data.resolve())]


# --------------------------------------------------------------------------- engine_versions


def test_engine_versions_come_from_the_installed_distributions():
    r = run(ok, engines=("numpy", "pint"))
    assert r.provenance.engine_versions == {"numpy": md.version("numpy"), "pint": md.version("pint")}


def test_a_distribution_that_is_not_installed_is_left_out():
    r = run(ok, engines=("numpy", "no-such-distribution-xyz"))
    assert r.provenance.engine_versions == {"numpy": md.version("numpy")}


def test_a_failed_call_still_reports_engine_versions():
    r = run(lambda job: (_ for _ in ()).throw(ToolFailure("input.invalid", "no")), engines=("numpy",))
    assert r.ok is False
    assert r.provenance.engine_versions == {"numpy": md.version("numpy")}


def test_the_default_is_no_engines():
    assert run(ok).provenance.engine_versions == {}


def test_result_json_in_the_job_directory_carries_the_provenance(tmp_path):
    data = tmp_path / "data.txt"
    data.write_text("data")
    r = run(lambda job: (job.input_file(str(data), param="a", extensions=(".txt",)), ok(job))[1], engines=("numpy",))
    saved = json.loads((Path(r.data["job_dir"]) / "result.json").read_text(encoding="utf-8"))
    assert saved["provenance"]["input_files"][0]["sha256"] == sha256(data)
    assert saved["provenance"]["engine_versions"] == {"numpy": md.version("numpy")}


# --------------------------------------------------------------------------- the tools that read files


@pytest.fixture
def cad(tmp_path):
    pytest.importorskip("OCP", reason="OCP is not installed; run: uv sync --extra cad")
    import cad_fixtures

    return cad_fixtures


def test_geometry_measure_lists_the_model_and_the_ocp_version(cad, tmp_path):
    from eng_toolkit.domains.geometry.tools import measure

    model = cad.write_step(cad.box(), tmp_path / "box.step")
    r = getattr(measure, "fn", measure)(model_path=str(model))
    assert r.ok, r.error
    (ref,) = r.provenance.input_files
    assert ref.path == str(model.resolve()) and ref.bytes == model.stat().st_size and ref.sha256 == sha256(model)
    assert r.provenance.engine_versions["cadquery-ocp-novtk"] == md.version("cadquery-ocp-novtk")
    assert r.provenance.engine_versions["numpy"] == md.version("numpy")


def test_geometry_measure_with_an_unsupported_extension_lists_nothing(tmp_path):
    from eng_toolkit.domains.geometry.tools import measure

    other = tmp_path / "part.obj"
    other.write_text("v 0 0 0\n")
    r = getattr(measure, "fn", measure)(model_path=str(other))
    assert r.error.family == "input.unsupported_format"
    assert r.provenance.input_files == []


def test_check_scan_lists_both_files_and_no_longer_repeats_them_in_data(cad, tmp_path):
    pytest.importorskip("open3d", reason="the scan engines are not installed; run: uv sync --extra scan")
    from eng_toolkit.domains.metrology.tools import check_scan

    model = cad.write_step(cad.box(), tmp_path / "box.step")
    scan = tmp_path / "scan.xyz"
    scan.write_text("".join(f"{10 + 8 * i} {10 + 3 * j} 20\n" for i in range(10) for j in range(10)))
    r = getattr(check_scan, "fn", check_scan)(
        scan_path=str(scan), scan_unit="mm", model_path=str(model), tolerance="0.1 mm"
    )
    assert r.ok, r.error
    assert [Path(ref.path) for ref in r.provenance.input_files] == [scan.resolve(), model.resolve()]
    assert [ref.sha256 for ref in r.provenance.input_files] == [sha256(scan), sha256(model)]
    assert [ref.bytes for ref in r.provenance.input_files] == [scan.stat().st_size, model.stat().st_size]
    assert "input_files" not in r.data
    assert set(r.provenance.engine_versions) == {"open3d", "cadquery-ocp-novtk"}


def test_check_scan_with_a_bad_model_extension_lists_only_the_scan(tmp_path):
    from eng_toolkit.domains.metrology.tools import check_scan

    scan = tmp_path / "scan.xyz"
    scan.write_text("1 2 3\n4 5 6\n")
    model = tmp_path / "model.iges"
    model.write_text("x")
    r = getattr(check_scan, "fn", check_scan)(
        scan_path=str(scan), scan_unit="mm", model_path=str(model), tolerance="0.1 mm"
    )
    assert r.error.family == "input.unsupported_format"
    assert [ref.sha256 for ref in r.provenance.input_files] == [sha256(scan)]
