import hashlib
import json
from pathlib import Path

from eng_toolkit.workspace import Workspace, resolve_root, sha256_file


def test_root_comes_from_env(isolated_workspace):
    assert resolve_root() == isolated_workspace


def test_job_dir_and_hashes(isolated_workspace):
    ws = Workspace()
    job = ws.new_job("calc_cylinder_force")

    assert job.dir.parent == isolated_workspace
    assert job.dir.is_dir()
    assert job.job_id.endswith("-calc_cylinder_force")

    art = job.write_inputs({"bore": "100 mm"})
    path = Path(art.path)
    assert path.name == "inputs.json"
    assert art.format == "json"
    assert art.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert art.sha256 == sha256_file(path)

    job.finalize({"ok": True, "provenance": {"inputs_hash": "sha256:abc"}})
    manifest = job.manifest()
    assert manifest["tool"] == "calc_cylinder_force"
    assert manifest["inputs_hash"] == "sha256:abc"
    assert set(manifest["files"]) == {"inputs.json", "result.json"}
    assert manifest["files"]["inputs.json"] == art.sha256
    assert json.loads((job.dir / "result.json").read_text(encoding="utf-8"))["ok"] is True


def test_two_jobs_get_distinct_dirs():
    ws = Workspace()
    a, b = ws.new_job("t"), ws.new_job("t")
    assert a.dir != b.dir


def test_is_writable(isolated_workspace):
    assert Workspace().is_writable() is True


def test_a_relative_env_root_is_made_absolute_where_it_is_read(tmp_path, monkeypatch):
    # UAT item 1: a relative ENG_TOOLKIT_WS broke every external program, which starts in the job folder
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENG_TOOLKIT_WS", "rel/ws")
    root = resolve_root()
    assert root.is_absolute() and root == (tmp_path / "rel" / "ws")
    ws = Workspace()
    assert ws.root.is_absolute()
    job = ws.new_job("t")
    assert job.dir.is_absolute() and job.dir.parent == tmp_path / "rel" / "ws"
    monkeypatch.chdir(tmp_path.parent)  # a later change of directory does not move the workspace
    assert Path(job.dir).is_dir()


def test_a_relative_explicit_root_is_absolute_too(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert Workspace("out").root == tmp_path / "out"
