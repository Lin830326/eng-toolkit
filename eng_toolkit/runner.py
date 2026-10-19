"""Shared execution wrapper used by every domain tool.

``run_tool`` does the bookkeeping the contract demands so that domain code only
has to parse units and compute:

1. open a workspace job ``ws/<job-id>/`` and write ``inputs.json``;
2. run the domain's ``compute`` callable;
3. convert :class:`eng_toolkit.errors.ToolFailure` (every ``UnitsError`` is one) into a structured ``ok=False``
   result (error family preserved) and any other exception into ``engine.failure``;
   failed results carry no ``warnings`` (standing warnings apply to successful results only);
4. attach provenance (engine, version, sha256 of canonical inputs, the input files the compute read, the
   installed versions of the engines it names) and artifacts;
5. write ``result.json`` + ``manifest.json`` and return the :class:`ToolResult`.
"""

from __future__ import annotations

import importlib.metadata as md
import os
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from eng_toolkit.contract import (
    Artifact,
    FileRef,
    Provenance,
    QuantityOut,
    ToolError,
    ToolResult,
    inputs_hash,
)
from eng_toolkit.errors import ToolFailure
from eng_toolkit.workspace import Workspace, sha256_file

ENV_READ_DIRS = "ENG_TOOLKIT_READ_DIRS"


@dataclass
class Computed:
    """What a domain ``compute`` function hands back to the runner."""

    values: dict[str, QuantityOut]
    primary: str
    warnings: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)


def run_tool(
    *,
    tool_name: str,
    engine: str,
    version: str,
    inputs: dict[str, Any],
    compute: Callable[["JobHandle"], Computed],
    workspace: Workspace | None = None,
    standing_warnings: tuple[str, ...] = (),
    engines: tuple[str, ...] = (),
) -> ToolResult:
    ws = workspace or Workspace()
    job = ws.new_job(tool_name)
    handle = JobHandle(job)
    artifacts: list[Artifact] = [job.write_inputs(inputs)]

    # ``standing_warnings`` describe the scope limits of a *result* (e.g. "ideal force, no seal
    # friction"). A failed call has no result, so it carries no warnings: attaching them would
    # make an error look like a computed value with caveats.
    try:
        computed = compute(handle)
    except ToolFailure as exc:
        result = ToolResult(
            ok=False,
            artifacts=artifacts,
            provenance=_provenance(engine, version, inputs, handle, engines),
            warnings=[],
            error=ToolError(family=exc.family, message=exc.message, details=exc.details),
        )
    except Exception as exc:  # noqa: BLE001 - engine failures must still honour the contract
        result = ToolResult(
            ok=False,
            artifacts=artifacts,
            provenance=_provenance(engine, version, inputs, handle, engines),
            warnings=[],
            error=ToolError(
                family="engine.failure",
                message=f"{type(exc).__name__}: {exc}",
                details={"traceback": traceback.format_exc(limit=5)},
            ),
        )
    else:
        if computed.primary not in computed.values:
            raise KeyError(f"primary {computed.primary!r} missing from values")
        result = ToolResult(
            ok=True,
            value=computed.values[computed.primary],
            values=computed.values,
            data=computed.data,
            artifacts=artifacts + computed.artifacts,
            provenance=_provenance(engine, version, inputs, handle, engines),
            warnings=list(standing_warnings) + computed.warnings,
        )

    result.data = {**result.data, "job_id": job.job_id, "job_dir": str(job.dir)}
    job.finalize(result.model_dump(mode="json"))
    return result


def _provenance(engine: str, version: str, inputs: dict[str, Any], handle: "JobHandle", engines: tuple[str, ...]) -> Provenance:
    versions: dict[str, str] = {}
    for dist in engines:
        try:
            versions[dist] = md.version(dist)
        except md.PackageNotFoundError:
            pass
    return Provenance(
        engine=engine,
        version=version,
        inputs_hash=inputs_hash(inputs),
        input_files=list(handle.input_files),
        engine_versions=versions,
    )


def read_dirs() -> list[Path] | None:
    """The directories files may be read from, or None when reading is not restricted.

    ``ENG_TOOLKIT_READ_DIRS`` holds directories separated by ``os.pathsep``.  Unset or empty means unrestricted, which is
    right for the default stdio transport (the server runs as the user who asked); set it when serving over HTTP, where a
    caller can name any path the server's user can read.
    """
    raw = os.environ.get(ENV_READ_DIRS, "").strip()
    if not raw:
        return None
    return [Path(item).expanduser().resolve() for item in raw.split(os.pathsep) if item.strip()]


def _inside(path: Path, roots: list[Path]) -> bool:
    """True when ``path``, with symlinks and ``..`` resolved, lies below one of ``roots`` (case-insensitive on Windows)."""
    here = os.path.normcase(str(path.resolve()))
    for root in roots:
        base = os.path.normcase(str(root.resolve()))
        if here == base or here.startswith(base.rstrip(os.sep) + os.sep):
            return True
    return False


class JobHandle:
    """Thin wrapper so compute functions can emit extra artifacts into the job dir and name the files they read."""

    def __init__(self, job):
        self._job = job
        self.input_files: list[FileRef] = []

    @property
    def job_id(self) -> str:
        return self._job.job_id

    def input_file(
        self, raw: str, *, param: str, extensions: tuple[str, ...], max_bytes: int | None = None, too_large_family: str = "input.too_large"
    ) -> Path:
        """Check a caller's path and record the file (path, size, sha256) in ``provenance.input_files``.

        When ``ENG_TOOLKIT_READ_DIRS`` is set, only files below those directories (and below the workspace) are read:
        see :func:`read_dirs`.  Nothing is recorded for a path that fails a check.
        """
        path = Path(raw).expanduser()
        roots = read_dirs()
        if roots is not None and not _inside(path, [*roots, self._job.dir.parent]):
            raise ToolFailure(
                "input.path_not_allowed",
                f"{param}: this server only reads files below the directories named in {ENV_READ_DIRS} "
                f"({len(roots)} configured) and its workspace; the path is outside them",
                {"param": param, "allowed_directories": len(roots)},
            )
        if not path.is_file():
            raise ToolFailure("input.file_not_found", f"{param}: no such file {str(path)!r}", {"param": param, "path": str(path)})
        if path.suffix.lower() not in extensions:
            raise ToolFailure(
                "input.unsupported_format",
                f"{param}: {path.suffix or '(no extension)'!r} is not supported; use one of {list(extensions)}",
                {"param": param, "path": str(path), "supported": list(extensions)},
            )
        if max_bytes is not None and path.stat().st_size > max_bytes:
            raise ToolFailure(
                too_large_family,
                f"{param}: the file is {path.stat().st_size} bytes, over the {max_bytes} byte limit",
                {"param": param, "bytes": path.stat().st_size, "limit_bytes": max_bytes},
            )
        self.input_files.append(FileRef(path=str(path.resolve()), bytes=path.stat().st_size, sha256=sha256_file(path)))
        return path

    def write_json(self, filename: str, obj: Any) -> Artifact:
        return self._job.write_json(filename, obj)

    def write_text(self, filename: str, text: str, *, fmt: str) -> Artifact:
        return self._job.write_text(filename, text, fmt=fmt)

    def write_bytes(self, filename: str, payload: bytes, *, fmt: str) -> Artifact:
        return self._job.write_bytes(filename, payload, fmt=fmt)


__all__ = ["ENV_READ_DIRS", "Computed", "JobHandle", "read_dirs", "run_tool"]
