"""Per-call workspace: every tool invocation writes under ``ws/<job-id>/``.

Layout::

    ws/
      20261018T031500Z-3f9a2c1b-calc_cylinder_force/
        inputs.json      # raw inputs as received
        result.json      # the ToolResult that was returned
        manifest.json    # every file in the job dir with its sha256

Root resolution order: explicit ``root`` argument, ``ENG_TOOLKIT_WS`` env var,
then ``./ws`` relative to the current working directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eng_toolkit.contract import Artifact, canonical_json

ENV_ROOT = "ENG_TOOLKIT_WS"
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_root(root: str | os.PathLike[str] | None = None) -> Path:
    if root is not None:
        return Path(root)
    env = os.environ.get(ENV_ROOT)
    if env:
        return Path(env)
    return Path.cwd() / "ws"


class Job:
    """One tool call's directory.  Files written through it are hashed and recorded."""

    def __init__(self, root: Path, tool_name: str, job_id: str | None = None):
        safe_tool = _SAFE.sub("_", tool_name) or "tool"
        if job_id is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            job_id = f"{stamp}-{uuid.uuid4().hex[:8]}-{safe_tool}"
        self.job_id = job_id
        self.tool_name = tool_name
        self.dir = root / job_id
        self.dir.mkdir(parents=True, exist_ok=False)
        self._records: dict[str, str] = {}

    # -- writing -----------------------------------------------------------------
    def write_bytes(self, filename: str, payload: bytes, *, fmt: str) -> Artifact:
        path = self.dir / filename
        path.write_bytes(payload)
        digest = sha256_file(path)
        self._records[filename] = digest
        return Artifact(path=str(path), format=fmt, sha256=digest)

    def write_text(self, filename: str, text: str, *, fmt: str) -> Artifact:
        return self.write_bytes(filename, text.encode("utf-8"), fmt=fmt)

    def write_json(self, filename: str, obj: Any) -> Artifact:
        text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n"
        return self.write_text(filename, text, fmt="json")

    def write_inputs(self, inputs: dict[str, Any]) -> Artifact:
        return self.write_json("inputs.json", inputs)

    # -- finalising ---------------------------------------------------------------
    def finalize(self, result_dict: dict[str, Any]) -> Path:
        """Write ``result.json`` and ``manifest.json``.  Returns the manifest path."""
        self.write_json("result.json", result_dict)
        manifest = {
            "job_id": self.job_id,
            "tool": self.tool_name,
            "inputs_hash": result_dict.get("provenance", {}).get("inputs_hash"),
            "files": dict(sorted(self._records.items())),
        }
        manifest_path = self.dir / "manifest.json"
        manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
        return manifest_path

    def manifest(self) -> dict[str, Any]:
        return json.loads((self.dir / "manifest.json").read_text(encoding="utf-8"))


class Workspace:
    def __init__(self, root: str | os.PathLike[str] | None = None):
        self.root = resolve_root(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def new_job(self, tool_name: str) -> Job:
        return Job(self.root, tool_name)

    def is_writable(self) -> bool:
        probe = self.root / f".probe-{uuid.uuid4().hex[:8]}"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True
        except OSError:
            return False


__all__ = ["ENV_ROOT", "Job", "Workspace", "resolve_root", "sha256_file"]
