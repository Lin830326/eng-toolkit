"""Shared execution wrapper used by every domain tool.

``run_tool`` does the bookkeeping the contract demands so that domain code only
has to parse units and compute:

1. open a workspace job ``ws/<job-id>/`` and write ``inputs.json``;
2. run the domain's ``compute`` callable;
3. convert :class:`eng_toolkit.units.UnitsError` into a structured ``ok=False``
   result (error family preserved) and any other exception into ``engine.failure``;
4. attach provenance (engine, version, sha256 of canonical inputs) and artifacts;
5. write ``result.json`` + ``manifest.json`` and return the :class:`ToolResult`.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from eng_toolkit.contract import (
    Artifact,
    Provenance,
    QuantityOut,
    ToolError,
    ToolResult,
    inputs_hash,
)
from eng_toolkit.units import UnitsError
from eng_toolkit.workspace import Workspace


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
) -> ToolResult:
    ws = workspace or Workspace()
    job = ws.new_job(tool_name)
    handle = JobHandle(job)
    artifacts: list[Artifact] = [job.write_inputs(inputs)]
    provenance = Provenance(engine=engine, version=version, inputs_hash=inputs_hash(inputs))
    warnings: list[str] = list(standing_warnings)

    try:
        computed = compute(handle)
    except UnitsError as exc:
        result = ToolResult(
            ok=False,
            artifacts=artifacts,
            provenance=provenance,
            warnings=warnings,
            error=ToolError(family=exc.family, message=exc.message, details=exc.details),
        )
    except Exception as exc:  # noqa: BLE001 - engine failures must still honour the contract
        result = ToolResult(
            ok=False,
            artifacts=artifacts,
            provenance=provenance,
            warnings=warnings,
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
            provenance=provenance,
            warnings=warnings + computed.warnings,
        )

    result.data = {**result.data, "job_id": job.job_id, "job_dir": str(job.dir)}
    job.finalize(result.model_dump(mode="json"))
    return result


class JobHandle:
    """Thin wrapper so compute functions can emit extra artifacts into the job dir."""

    def __init__(self, job):
        self._job = job

    @property
    def job_id(self) -> str:
        return self._job.job_id

    def write_json(self, filename: str, obj: Any) -> Artifact:
        return self._job.write_json(filename, obj)

    def write_text(self, filename: str, text: str, *, fmt: str) -> Artifact:
        return self._job.write_text(filename, text, fmt=fmt)

    def write_bytes(self, filename: str, payload: bytes, *, fmt: str) -> Artifact:
        return self._job.write_bytes(filename, payload, fmt=fmt)


__all__ = ["Computed", "JobHandle", "run_tool"]
