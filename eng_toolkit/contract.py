"""Unified return contract for every eng-toolkit tool.

Every tool, in every domain, returns a :class:`ToolResult`.  The shape is::

    {
      "ok": true,
      "value":   {"quantity": 164933.6, "unit": "newton", "uncertainty": null},
      "values":  {"push_force": {...}, "pull_force": {...}},
      "data":    {},                       # non-quantity payload (doctor reports, listings)
      "artifacts": [{"path": "...", "format": "json", "sha256": "..."}],
      "provenance": {"engine": "eng_toolkit.calc.hydraulics", "version": "0.0.1",
                     "inputs_hash": "sha256:..."},
      "warnings": [],
      "error": null,
      "review_status": "unreviewed"
    }

``value`` is the primary result; ``values`` holds every named result (including the
primary) so multi-output tools stay inside the contract.  ``review_status`` is a
literal: nothing this toolkit produces has been through engineering review.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

REVIEW_STATUS_UNREVIEWED = "unreviewed"
"""Every result carries this.  Human engineering sign-off happens outside the toolkit."""


class QuantityOut(BaseModel):
    """A magnitude with its unit and (optional) one-sigma absolute uncertainty in the same unit."""

    model_config = ConfigDict(extra="forbid")

    quantity: float
    unit: str = Field(description="pint unit string, e.g. 'newton', 'bar', 'millimeter'")
    uncertainty: float | None = Field(
        default=None,
        description="Absolute uncertainty in the same unit; null = not estimated",
    )


class Artifact(BaseModel):
    """A file written to the workspace for this call, with its content hash."""

    model_config = ConfigDict(extra="forbid")

    path: str
    format: str = Field(description="File format tag, e.g. 'json', 'step', 'dxf', 'csv'")
    sha256: str


class Provenance(BaseModel):
    """What produced the result and from which inputs."""

    model_config = ConfigDict(extra="forbid")

    engine: str = Field(
        description="Engine identifier, e.g. 'eng_toolkit.calc.hydraulics' or 'fluids'"
    )
    version: str
    inputs_hash: str = Field(description="'sha256:<hex>' of the canonical JSON of the inputs")


class ToolError(BaseModel):
    """Structured error. ``family`` is a stable machine-readable category."""

    model_config = ConfigDict(extra="forbid")

    family: str = Field(
        description=(
            "Stable error family, e.g. 'units.bare_number', 'units.dimension_mismatch', "
            "'units.parse_error', 'input.invalid', 'engine.failure'"
        )
    )
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """The one shape every tool returns."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    value: QuantityOut | None = Field(default=None, description="Primary quantity result")
    values: dict[str, QuantityOut] = Field(
        default_factory=dict, description="All named quantity results (includes the primary)"
    )
    data: dict[str, Any] = Field(
        default_factory=dict, description="Non-quantity payload (reports, listings)"
    )
    artifacts: list[Artifact] = Field(default_factory=list)
    provenance: Provenance
    warnings: list[str] = Field(default_factory=list)
    error: ToolError | None = None
    review_status: Literal["unreviewed"] = REVIEW_STATUS_UNREVIEWED


# --------------------------------------------------------------------------- helpers


def canonical_json(obj: Any) -> str:
    """Deterministic JSON used for hashing inputs (sorted keys, fixed separators)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def inputs_hash(inputs: dict[str, Any]) -> str:
    """``sha256:<hex>`` of the canonical JSON of ``inputs``."""
    digest = hashlib.sha256(canonical_json(inputs).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def failure(
    *,
    family: str,
    message: str,
    engine: str,
    version: str,
    inputs: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> ToolResult:
    """Build an ``ok=False`` result that still satisfies the full contract."""
    return ToolResult(
        ok=False,
        provenance=Provenance(engine=engine, version=version, inputs_hash=inputs_hash(inputs or {})),
        warnings=list(warnings or []),
        error=ToolError(family=family, message=message, details=details or {}),
    )


__all__ = [
    "REVIEW_STATUS_UNREVIEWED",
    "Artifact",
    "Provenance",
    "QuantityOut",
    "ToolError",
    "ToolResult",
    "canonical_json",
    "failure",
    "inputs_hash",
]
