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
from eng_toolkit import safefile
from eng_toolkit.workspace import Workspace

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
            error=ToolError(family=exc.family, message=handle.restore_paths(exc.message), details=handle.restore_paths(exc.details)),
        )
    except Exception as exc:  # noqa: BLE001 - engine failures must still honour the contract
        result = ToolResult(
            ok=False,
            artifacts=artifacts,
            provenance=_provenance(engine, version, inputs, handle, engines),
            warnings=[],
            error=ToolError(
                family="engine.failure",
                message=handle.restore_paths(f"{type(exc).__name__}: {exc}"),
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
    versions.update(handle.engine_versions)
    return Provenance(
        engine=engine,
        version=version,
        inputs_hash=inputs_hash(inputs),
        input_files=list(handle.input_files),
        engine_versions=versions,
    )


def read_dirs() -> list[Path] | None:
    """The directories files may be read from, or None when reading is not restricted.

    ``ENG_TOOLKIT_READ_DIRS`` holds directories separated by ``os.pathsep``.  Unset means unrestricted, which is right for
    the default stdio transport (the server runs as the user who asked); set it when serving over HTTP, where a caller can
    name any path the server's user can read.  Set but empty (or only separators and blanks) is a list with no directory:
    only the server's own workspace can be read.  An operator who blanked the list wants fewer files readable, never more,
    so an empty value must not fall back to unrestricted.
    """
    raw = os.environ.get(ENV_READ_DIRS)
    if raw is None:
        return None
    return [Path(item.strip()).expanduser().resolve() for item in raw.split(os.pathsep) if item.strip()]


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
        self._copies: dict[str, str] = {}  # the copy a tool reads -> the path the caller named
        self.engine_versions: dict[str, str] = {}  # versions of programs that are not Python distributions (an external executable's), added to the provenance

    @property
    def job_id(self) -> str:
        return self._job.job_id

    @property
    def job_dir(self) -> Path:
        return self._job.dir

    def input_file(
        self, raw: str, *, param: str, extensions: tuple[str, ...], max_bytes: int | None = None, too_large_family: str = "input.too_large"
    ) -> Path:
        """Open a caller's file once and return the path of a copy of exactly those bytes, in this job's ``input/`` directory.

        The file is opened one time; the checks that matter (``ENG_TOOLKIT_READ_DIRS``, the size cap) are made on the *open*
        file, whose real location the system reports (see :mod:`eng_toolkit.safefile`), and the copy is hashed as it is
        written.  A tool that reads the returned path therefore reads the bytes that were checked and recorded, whatever happens
        to the caller's path afterwards (a directory swapped for a link to a place outside the allowed ones, say).
        ``provenance.input_files`` records the real path of the original, its size and the sha256 of the bytes.  Nothing is
        recorded for a path that fails a check.
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
        gone = ToolFailure("input.file_not_found", f"{param}: no such file {str(path)!r}", {"param": param, "path": str(path)})
        try:
            fd = safefile.open_regular(path)
        except OSError as exc:
            raise gone from exc
        try:
            try:
                real = safefile.final_path(fd, path)
            except OSError as exc:
                raise gone from exc
            if roots is not None and not safefile.within(real, [*roots, self._job.dir.parent]):
                raise ToolFailure(
                    "input.path_not_allowed",
                    f"{param}: this server only reads files below the directories named in {ENV_READ_DIRS} "
                    f"({len(roots)} configured) and its workspace; the path is outside them",
                    {"param": param, "allowed_directories": len(roots)},
                )
            size = os.fstat(fd).st_size
            too_large = lambda n: ToolFailure(  # noqa: E731
                too_large_family, f"{param}: the file is {n} bytes, over the {max_bytes} byte limit", {"param": param, "bytes": n, "limit_bytes": max_bytes}
            )
            if max_bytes is not None and size > max_bytes:
                raise too_large(size)
            copy = self._job.dir / "input" / f"{len(self.input_files) + 1}-{path.name}"
            try:
                copied, digest = safefile.copy_from(fd, copy, max_bytes)
            except safefile.TooLarge as exc:
                raise too_large(exc.size) from exc
        finally:
            os.close(fd)
        self.input_files.append(FileRef(path=real, bytes=copied, sha256=digest))
        self._copies[str(copy)] = str(path)
        return copy

    def restore_paths(self, value: Any) -> Any:
        """``value`` with every mention of an input's job-directory copy replaced by the path the caller named.

        A tool reads the copy made by :meth:`input_file`, so its own errors name the copy; the caller never saw that path
        and cannot map it to their file.  Strings are rewritten in plain and in repr (doubled backslash) form, lists and
        dicts recursively; anything else is returned unchanged.
        """
        if isinstance(value, str):
            for copy, original in sorted(self._copies.items(), key=lambda item: -len(item[0])):
                value = value.replace(copy, original).replace(copy.replace("\\", "\\\\"), original.replace("\\", "\\\\"))
            return value
        if isinstance(value, list):
            return [self.restore_paths(item) for item in value]
        if isinstance(value, dict):
            return {key: self.restore_paths(item) for key, item in value.items()}
        return value

    def write_json(self, filename: str, obj: Any) -> Artifact:
        return self._job.write_json(filename, obj)

    def write_text(self, filename: str, text: str, *, fmt: str) -> Artifact:
        return self._job.write_text(filename, text, fmt=fmt)

    def write_bytes(self, filename: str, payload: bytes, *, fmt: str) -> Artifact:
        return self._job.write_bytes(filename, payload, fmt=fmt)


__all__ = ["ENV_READ_DIRS", "Computed", "JobHandle", "read_dirs", "run_tool"]
