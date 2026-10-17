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
