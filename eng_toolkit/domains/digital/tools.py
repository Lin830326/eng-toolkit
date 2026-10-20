"""``digital`` sub-server: Verilog synthesis, lint and equivalence checking with Yosys.

Three tools.  Each takes Verilog as text or as files, passes it through :mod:`.guard` (the caller's text is data and never a Yosys
command), writes a Yosys script from fixed templates and runs Yosys through :mod:`eng_toolkit.external` (job directory, limits,
scrubbed environment).  The long version, with what is exact and what is not, is ``docs/digital/design.md``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.digital import equiv, flow, guard, lint, metrics
from eng_toolkit.errors import ToolFailure
from eng_toolkit.runner import Computed, JobHandle, run_tool

ENGINE_SYNTHESIZE = "eng_toolkit.digital.synthesize"
ENGINE_CHECK = "eng_toolkit.digital.check"
ENGINE_EQUIVALENT = "eng_toolkit.digital.equivalent"
TOOL_NAMES: tuple[str, ...] = ("synthesize", "check", "equivalent")
MAX_FILES = 20
SOURCE_SUFFIXES = (".v", ".sv")
TARGETS: dict[str, tuple[str, ...]] = {
    "structural": ("synth -top {top} -noabc",),
    "optimized": ("synth -top {top}",),
    "abc_simple": ("synth -top {top} -noabc", "abc -g simple", "opt_clean"),
    "abc_gates": ("synth -top {top} -noabc", "abc -g gates", "opt_clean"),
    "abc_aig": ("synth -top {top} -noabc", "abc -g aig", "opt_clean"),
    "abc_cmos2": ("synth -top {top} -noabc", "abc -g cmos2", "opt_clean"),
    "abc_nand": ("synth -top {top} -noabc", "abc -g NAND", "opt_clean"),
}
TARGET_TEXT = {
    "structural": "no ABC: each operator is expanded to simple gates and the structure of the source is kept (exactly derivable by hand)",
    "optimized": "Yosys's default synth, which ends with ABC's fast script (smaller, but the exact count depends on ABC and the Yosys version)",
    "abc_simple": "ABC maps to AND, OR, XOR, MUX",
    "abc_gates": "ABC maps to AND, NAND, OR, NOR, XOR, XNOR, ANDNOT, ORNOT",
    "abc_aig": "ABC maps to AND, NAND, OR, NOR, ANDNOT, ORNOT",
    "abc_cmos2": "ABC maps to NAND, NOR and NOT",
    "abc_nand": "ABC maps to NAND and NOT only",
}
STANDING = (
    "Synthesis, lint and equivalence results come from Yosys on the Verilog given; they are not a sign-off. Check the design with your own simulation and the vendor's flow before it is built.",
)

server = FastMCP(
    "digital",
    instructions=(
        "Verilog synthesis, lint and equivalence checking with Yosys (a program the user installed). The Verilog you give is data: "
        "`include, file-touching system tasks and anything that could run a command are refused. "
        "Results are unreviewed engineering estimates and must be checked by a qualified engineer."
    ),
)


def _dimensionless(value: float) -> QuantityOut:
    return QuantityOut(quantity=float(value), unit="dimensionless")


def split_paths(raw: str, *, param: str) -> list[str]:
    """The paths in ``raw``: a JSON array of strings, or paths separated by ';' or line breaks.  At most :data:`MAX_FILES`."""
    text = raw.strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            items = json.loads(text)
        except ValueError as exc:
            raise ToolFailure("input.invalid", f"{param}: not a valid JSON list of paths", {"param": param}) from exc
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            raise ToolFailure("input.invalid", f"{param}: a JSON list must hold only path strings", {"param": param})
    else:
        items = re.split(r"[;\n]", text)
    paths = [i.strip() for i in items if i.strip()]
    if len(paths) > MAX_FILES:
        raise ToolFailure("input.out_of_range", f"{param}: {len(paths)} files is over the limit of {MAX_FILES}", {"param": param, "max_files": MAX_FILES})
    for path in paths:
        if "\x00" in path:
            raise ToolFailure("input.invalid", f"{param}: a path holds a NUL character", {"param": param})
    return paths


def gather(job: JobHandle, *, source: str, files: str, param_source: str, param_files: str, first_index: int, inline_label: str) -> list[guard.Source]:
    """The checked sources of one design: the inline text first, then each file (read through ``JobHandle.input_file``)."""
    paths = split_paths(files, param=param_files)
    if not source.strip() and not paths:
        raise ToolFailure("input.invalid", f"give Verilog in {param_source} or file paths in {param_files}", {"param": param_source})
    sources: list[guard.Source] = []
    total = 0

    def add(raw: bytes | str, label: str) -> None:
        nonlocal total
        checked = guard.sanitize(raw, label=label, name=f"src{first_index + len(sources)}.v", limit=guard.MAX_SOURCE_BYTES)
        total += len(checked.text.encode("utf-8"))
        if total > guard.MAX_SOURCE_BYTES:
            raise ToolFailure(
                "input.too_large",
                f"the sources of one design total over {guard.MAX_SOURCE_BYTES} bytes",
                {"limit_bytes": guard.MAX_SOURCE_BYTES},
            )
        sources.append(checked)

    if source.strip():
        add(source, inline_label)
    for index, raw in enumerate(paths):
        copy = job.input_file(raw, param=param_files, extensions=SOURCE_SUFFIXES, max_bytes=guard.MAX_SOURCE_BYTES)
        add(Path(copy).read_bytes(), Path(raw).name or f"{param_files}[{index}]")
    modules = sum(len(s.modules) for s in sources)
    if modules > guard.MAX_MODULES:
        raise ToolFailure("input.out_of_range", f"{modules} modules is over the limit of {guard.MAX_MODULES} per design", {"max_modules": guard.MAX_MODULES})
    return sources


def _sources_data(sources: list[guard.Source]) -> list[dict[str, Any]]:
    return [{"file": s.label, "bytes": len(s.text.encode("utf-8")), "modules": list(s.modules)[:50]} for s in sources]


def _yosys_data(out: flow.Outcome, job: JobHandle, script_artifact: Any) -> dict[str, Any]:
    return {
        "version": out.version,
        "found_via": out.record.source.split(":")[0],
        "seconds": round(out.seconds, 3),
        "script_artifact": script_artifact.path,
        "limits": {"timeout_s": out.record.limits.get("timeout_s"), "memory_mb": flow.MEMORY_MB, "memory_cap_applied": out.record.memory_cap_applied},
    }


def _warning_texts(warnings: list[dict[str, Any]], limit: int = 20) -> list[str]:
    seen: list[str] = []
    for item in warnings:
        text = item["message"]
        if text not in seen:
            seen.append(text)
    return seen[:limit]


# --------------------------------------------------------------------------- digital_synthesize


@server.tool(
    name="synthesize",
    description=(
        "Synthesise Verilog with Yosys to a generic gate-level netlist and report what came out: cell counts by type, wires, the "
        "longest combinational path in gate levels (Yosys ltp, flip-flops cut), and a unit-gate area estimate (a toolkit convention: "
        "NOT/AND/OR/NAND/NOR 1, XOR/XNOR 2, MUX 3, flip-flop 5; see data.area_model; not silicon area). Give Verilog as text in "
        "'source' or as file paths in 'files' (.v/.sv, up to 20, 1 MB in all) and the name of the top module. 'target': 'structural' "
        "(no optimiser: the operators are expanded and the count follows the source exactly), 'optimized' (Yosys's default synth with "
        "ABC; smaller, but the count depends on ABC and the Yosys version), or 'abc_simple', 'abc_gates', 'abc_aig', 'abc_cmos2', "
        "'abc_nand' (a chosen generic gate library). Same input, same Yosys version: same counts. The gate-level netlist (Verilog and "
        "JSON), the Yosys script and the log tail are written as artifacts. Needs Yosys installed by the user (see toolkit_doctor). "
        "`include and file-touching system tasks in the source are refused."
    ),
)
def synthesize(
    top: Annotated[str, Field(description="Name of the top module (letters, digits, underscore; at most 64 characters)")],
    source: Annotated[str, Field(description="Verilog source text. Empty if 'files' is given")] = "",
    files: Annotated[
        str, Field(description="Paths of Verilog files (.v or .sv), separated by ';' or line breaks, or a JSON list. Up to 20; 1 MB in all with 'source'")
    ] = "",
    target: Annotated[
        str, Field(description="'optimized' (default), 'structural', 'abc_simple', 'abc_gates', 'abc_aig', 'abc_cmos2' or 'abc_nand'")
    ] = "optimized",
    language: Annotated[str, Field(description="'verilog' (Verilog-2005, default) or 'systemverilog' (Yosys's limited SystemVerilog subset)")] = "verilog",
    timeout_seconds: Annotated[int, Field(description="Time limit for Yosys, 1 to 120 seconds (default 30); a design that needs more is refused")] = flow.DEFAULT_TIMEOUT_S,
) -> ToolResult:
    inputs = {"top": top, "source": source, "files": files, "target": target, "language": language, "timeout_seconds": timeout_seconds}

    def compute(job: JobHandle) -> Computed:
        name = guard.plain_identifier(top, param="top")
        if target not in TARGETS:
            raise ToolFailure("input.invalid", f"target must be one of {list(TARGETS)}, got {target[:30]!r}", {"param": "target", "allowed": list(TARGETS)})
        limit = flow.timeout_seconds(timeout_seconds)
        flow.language_flag(language)
        sources = gather(job, source=source, files=files, param_source="source", param_files="files", first_index=0, inline_label="source")
        guard.find_module(sources, name, param="top")
        script = [
            *flow.read_lines(sources, language),
            f"hierarchy -check -top {name}",
            "proc",
            "flatten -noscopeinfo",
            *(line.format(top=name) for line in TARGETS[target]),
            "stat",
            "tee -o stat.json stat -json",
            "ltp -noff",
            "write_verilog -noattr netlist.v",
            "write_json netlist.json",
        ]
        out = flow.run_yosys(job, sources, script, timeout_s=limit)
        script_artifact = job.write_text("yosys-script.ys", out.script, fmt="yosys-script")
        flow.raise_if_failed(out, what="synthesis")
        stat = out.json_file("stat.json")
        stats = metrics.module_stats(stat, name)
        if stats is None:
            raise ToolFailure("engine.failure", "Yosys finished but its statistics for the top module could not be read", {"top": name})
        by_type = metrics.cells_by_type(stats)
        total_cells = sum(by_type.values())
        area, unrated = metrics.area_estimate(by_type)
        warnings_found, _errors = flow.messages(out)
        levels, path = metrics.longest_path(out.log, name)
        warnings: list[str] = list(STANDING)
        loops = [w for w in warnings_found if w["rule"] == "combinational-loop"]
        if loops:
            levels, path = None, []
            warnings.append("The design has a combinational loop, so the longest path is not defined and is not reported (run digital_check for the loop).")
        elif levels is None:
            warnings.append("Yosys printed no longest-path report for the top module; the level count is not available.")
        if unrated:
            warnings.append(f"Cell types the area table does not rate are counted as cells but left out of the area: {', '.join(sorted(unrated))}.")
        values = {
            "cells": _dimensionless(total_cells),
            "wires": _dimensionless(int(stats.get("num_wires", 0))),
            "wire_bits": _dimensionless(int(stats.get("num_wire_bits", 0))),
            "area_unit_gates": _dimensionless(area),
        }
        if levels is not None:
            values["levels"] = _dimensionless(levels)
        artifacts = [script_artifact, job.write_text("yosys-log-tail.txt", flow.log_tail(out), fmt="text")]
        for filename, fmt in (("netlist.v", "verilog"), ("netlist.json", "json"), ("stat.json", "json")):
            text = out.file_text(filename, limit=20_000_000)
            if text is None:
                warnings.append(f"{filename} was not written or is over 20 MB and is not attached.")
            else:
                artifacts.append(job.write_text(filename, text, fmt=fmt))
        return Computed(
            values=values,
            primary="cells",
            warnings=warnings,
            data={
                "top": name,
                "target": target,
                "target_meaning": TARGET_TEXT[target],
                "language": language.strip().lower(),
                "sources": _sources_data(sources),
                "cells_by_type": by_type,
                "unrated_cells": unrated,
                "public_wires": int(stats.get("num_pub_wires", 0)),
                "ports": int(stats.get("num_ports", 0)),
                "levels_definition": "cells on the longest path between a primary input or flip-flop output and a primary output or flip-flop input (Yosys ltp -noff)",
                "longest_path": path,
                "area_model": metrics.area_model(),
                "yosys_warnings": _warning_texts(warnings_found),
                "yosys": _yosys_data(out, job, script_artifact),
            },
            artifacts=artifacts,
        )

    return run_tool(tool_name="digital_synthesize", engine=ENGINE_SYNTHESIZE, version=__version__, inputs=inputs, compute=compute)


# --------------------------------------------------------------------------- digital_check


@server.tool(
    name="check",
    description=(
        "Lint Verilog with Yosys, without synthesis goals: syntax errors with file and line, hierarchy problems (missing module, wrong "
        "port name), undriven wires, wires with several drivers, latches inferred by an incomplete if or case, combinational loops "
        "(also through module boundaries), implicit declarations (a misspelt name that became a wire) and port width mismatches. Each "
        "finding has severity ('error' or 'warning'), a rule id, file, line (null when Yosys gave none), module, signal and a hint. "
        "The call succeeds (ok=true) when the check ran, even if it found errors: read data.findings and values.errors. A syntax error "
        "is one finding (Yosys stops at the first). Give Verilog as text in 'source' or as file paths in 'files' (.v/.sv, up to 20). "
        "'top' is optional (empty = check every module). Not a full linter: it reports what Yosys reports. Needs Yosys installed by "
        "the user. `include and file-touching system tasks in the source are refused."
    ),
)
def check(
    source: Annotated[str, Field(description="Verilog source text. Empty if 'files' is given")] = "",
    files: Annotated[
        str, Field(description="Paths of Verilog files (.v or .sv), separated by ';' or line breaks, or a JSON list. Up to 20; 1 MB in all with 'source'")
    ] = "",
    top: Annotated[str, Field(description="Name of the top module; empty = check all modules")] = "",
    language: Annotated[str, Field(description="'verilog' (Verilog-2005, default) or 'systemverilog'")] = "verilog",
    timeout_seconds: Annotated[int, Field(description="Time limit for Yosys, 1 to 120 seconds (default 30)")] = flow.DEFAULT_TIMEOUT_S,
) -> ToolResult:
    inputs = {"source": source, "files": files, "top": top, "language": language, "timeout_seconds": timeout_seconds}

    def compute(job: JobHandle) -> Computed:
        name = guard.plain_identifier(top, param="top") if top.strip() else None
        limit = flow.timeout_seconds(timeout_seconds)
        flow.language_flag(language)
        sources = gather(job, source=source, files=files, param_source="source", param_files="files", first_index=0, inline_label="source")
        if name:
            guard.find_module(sources, name, param="top")
        out = flow.run_yosys(job, sources, lint.check_script(sources, name, language), timeout_s=limit)
        script_artifact = job.write_text("yosys-script.ys", out.script, fmt="yosys-script")
        findings, truncated = lint.findings(out)
        if out.record.returncode != 0 and not any(f["severity"] == "error" for f in findings):
            flow.raise_if_failed(out, what="the check")
        errors = sum(1 for f in findings if f["severity"] == "error")
        warn = sum(1 for f in findings if f["severity"] == "warning")
        artifacts = [script_artifact, job.write_json("findings.json", findings), job.write_text("yosys-log-tail.txt", flow.log_tail(out), fmt="text")]
        warnings = list(STANDING)
        if truncated:
            warnings.append(f"More than {lint.MAX_FINDINGS} findings: only the first {lint.MAX_FINDINGS} are listed.")
        if out.record.returncode != 0:
            warnings.append("Yosys stopped at an error, so the checks after that point did not run: fix it and call again.")
        return Computed(
            values={"errors": _dimensionless(errors), "warnings": _dimensionless(warn)},
            primary="errors",
            warnings=warnings,
            data={
                "clean": not findings,
                "top": name,
                "language": language.strip().lower(),
                "sources": _sources_data(sources),
                "findings": findings,
                "findings_truncated": truncated,
                "rules": sorted(lint.HINTS),
                "yosys": _yosys_data(out, job, script_artifact),
            },
            artifacts=artifacts,
        )

    return run_tool(tool_name="digital_check", engine=ENGINE_CHECK, version=__version__, inputs=inputs, compute=compute)


# --------------------------------------------------------------------------- digital_equivalent


@server.tool(
    name="equivalent",
    description=(
        "Check whether two Verilog designs (or two top modules of one source) behave the same, with Yosys: they are joined in a miter "
        "and a SAT solver looks for an input that makes an output differ. 'cycles' = 0: combinational proof over all input values (the "
        "designs must hold no flip-flop, latch or memory; otherwise it is refused). 'cycles' = N from 1 to 100: a BOUNDED check of N "
        "time steps (step k = state after k clock edges, all inputs free in every step, every flip-flop without an initial value "
        "starting at 0): no mismatch within N steps is NOT a proof of equivalence, only that none exists in the first N steps. "
        "The result data.verdict is 'equivalent' (proved), 'not_equivalent' (with data.counterexample: input values, both designs' "
        "outputs, and for a bounded check the cycle) or 'no_mismatch_within_bound'. Both designs must have the same ports. Give "
        "design A as 'source_a' or 'files_a' with 'top_a'; design B as 'source_b' or 'files_b' with 'top_b'. Leave B's sources empty to "
        "compare two top modules ('top_a', 'top_b') defined in A's sources. Needs Yosys installed by the user. `include and "
        "file-touching system tasks in the source are refused."
    ),
)
def equivalent(
    top_a: Annotated[str, Field(description="Top module of design A")],
    source_a: Annotated[str, Field(description="Verilog text of design A. Empty if files_a is given")] = "",
    files_a: Annotated[str, Field(description="Paths of design A's files (.v/.sv), separated by ';' or line breaks, or a JSON list")] = "",
    top_b: Annotated[str, Field(description="Top module of design B. Empty = the same name as top_a")] = "",
    source_b: Annotated[str, Field(description="Verilog text of design B. Empty (with files_b empty) = design B is another top in design A's sources")] = "",
    files_b: Annotated[str, Field(description="Paths of design B's files (.v/.sv), separated by ';' or line breaks, or a JSON list")] = "",
    cycles: Annotated[
        int, Field(description="0 = combinational proof (no flip-flops allowed); 1 to 100 = bounded check of that many time steps (not a proof)")
    ] = 0,
    language: Annotated[str, Field(description="'verilog' (Verilog-2005, default) or 'systemverilog'")] = "verilog",
    timeout_seconds: Annotated[int, Field(description="Time limit for Yosys, 1 to 120 seconds (default 30)")] = flow.DEFAULT_TIMEOUT_S,
) -> ToolResult:
    inputs = {
        "top_a": top_a,
        "source_a": source_a,
        "files_a": files_a,
        "top_b": top_b,
        "source_b": source_b,
        "files_b": files_b,
        "cycles": cycles,
        "language": language,
        "timeout_seconds": timeout_seconds,
    }

    def compute(job: JobHandle) -> Computed:
        name_a = guard.plain_identifier(top_a, param="top_a")
        name_b = guard.plain_identifier(top_b, param="top_b") if top_b.strip() else name_a
        if isinstance(cycles, bool) or not isinstance(cycles, int) or not 0 <= cycles <= equiv.MAX_CYCLES:
            raise ToolFailure(
                "input.out_of_range", f"cycles must be a whole number from 0 to {equiv.MAX_CYCLES}, got {cycles!r}", {"param": "cycles", "min": 0, "max": equiv.MAX_CYCLES}
            )
        limit = flow.timeout_seconds(timeout_seconds)
        flow.language_flag(language)
        sources_a = gather(job, source=source_a, files=files_a, param_source="source_a", param_files="files_a", first_index=0, inline_label="source_a")
        guard.find_module(sources_a, name_a, param="top_a")
        same_sources = not source_b.strip() and not files_b.strip()
        if same_sources:
            if name_b == name_a:
                raise ToolFailure("input.invalid", "design B is empty and top_b is the same as top_a: give source_b/files_b or a different top_b", {"param": "top_b"})
            sources_b = sources_a
            guard.find_module(sources_b, name_b, param="top_b")
        else:
            sources_b = gather(job, source=source_b, files=files_b, param_source="source_b", param_files="files_b", first_index=len(sources_a), inline_label="source_b")
            guard.find_module(sources_b, name_b, param="top_b")
        written = sources_a + ([] if same_sources else sources_b)
        out = flow.run_yosys(job, written, equiv.equiv_script(sources_a, name_a, sources_b, name_b, language, cycles), timeout_s=limit)
        script_artifact = job.write_text("yosys-script.ys", out.script, fmt="yosys-script")
        state = metrics.has_state(out.json_file("state.json"))
        if state and cycles == 0:
            raise ToolFailure(
                "input.invalid",
                f"the designs hold state ({', '.join(state[:5])}); a combinational proof does not apply. Pass cycles from 1 to {equiv.MAX_CYCLES} for a bounded check",
                {"param": "cycles", "state_cells": state[:10]},
            )
        result = equiv.verdict(out.log)
        if result is None:
            flow.raise_if_failed(out, what="the equivalence check")
            raise ToolFailure("engine.failure", "Yosys finished without a SAT verdict", {"yosys_message": out.clean(out.log[-300:])})
        bounded = cycles > 0
        warnings = list(STANDING)
        data: dict[str, Any] = {
            "top_a": name_a,
            "top_b": name_b,
            "mode": "bounded" if bounded else "combinational",
            "bound_cycles": cycles if bounded else None,
            "initial_state": "every flip-flop without an initial value starts at 0" if bounded else None,
            "sources_a": _sources_data(sources_a),
            "sources_b": _sources_data(sources_b),
            "state_cells": state,
            "yosys": _yosys_data(out, job, script_artifact),
        }
        if result == "counterexample":
            example = equiv.counterexample(out.log, sequential=bounded)
            data.update(verdict="not_equivalent", equivalent=False, proof="counterexample", counterexample=example)
            mismatch = 1.0
        elif bounded:
            data.update(verdict="no_mismatch_within_bound", equivalent=None, proof="bounded", counterexample=None)
            warnings.append(
                f"BOUNDED check: no output differs in the first {cycles} time steps (0 to {cycles - 1}). That is not a proof of equivalence: "
                f"the designs may differ at step {cycles} or later. Raise 'cycles' or prove it another way."
            )
            mismatch = 0.0
        else:
            data.update(verdict="equivalent", equivalent=True, proof="complete", counterexample=None)
            mismatch = 0.0
        artifacts = [script_artifact, job.write_text("yosys-log-tail.txt", flow.log_tail(out), fmt="text")]
        if data["counterexample"] is not None:
            artifacts.append(job.write_json("counterexample.json", data["counterexample"]))
        values = {"mismatch_found": _dimensionless(mismatch)}
        if bounded:
            values["checked_cycles"] = _dimensionless(cycles)
        return Computed(values=values, primary="mismatch_found", warnings=warnings, data=data, artifacts=artifacts)

    return run_tool(tool_name="digital_equivalent", engine=ENGINE_EQUIVALENT, version=__version__, inputs=inputs, compute=compute)


__all__ = ["TOOL_NAMES", "server"]
