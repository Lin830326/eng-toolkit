"""The four computations behind the ``pcb`` tools: board facts from our own reader, DRC, ERC, and exports through kicad-cli."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from eng_toolkit.contract import QuantityOut
from eng_toolkit.domains.pcb import boardread, program, schread, sexp
from eng_toolkit.errors import ToolFailure
from eng_toolkit.runner import Computed, JobHandle
from eng_toolkit.units import parse_quantity

MM = "millimeter"
COUNT = "dimensionless"
MAX_LISTED = 1000
EXPORTS = ("gerbers", "drill", "pos", "pdf", "svg")
LAYER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.]{0,23}\Z")
MAX_LAYERS = 40
PLOT_LAYER = re.compile(r"(F|B)\.(Cu|Mask|SilkS|Paste)\Z|In\d{1,2}\.Cu\Z|Edge\.Cuts\Z")
SEVERITY_ORDER = {"error": 0, "warning": 1, "exclusion": 2}


def _count(n: int | float) -> QuantityOut:
    return QuantityOut(quantity=float(n), unit=COUNT)


def _mm(x: float) -> QuantityOut:
    return QuantityOut(quantity=float(x), unit=MM)


def _bad(message: str, family: str = "input.invalid", **details: Any) -> ToolFailure:
    return ToolFailure(family, message, details)


def _check_finite(value: Any, where: str) -> None:
    for number in boardread.iter_finite(value):
        if not math.isfinite(number):
            raise ToolFailure("engine.failure", f"a non-finite number reached the {where}; it was not returned", {})


def _board_text(copy: Path) -> str:
    try:
        return sexp.decode(copy.read_bytes())
    except sexp.SexpError as exc:
        raise ToolFailure("input.invalid", f"not a readable KiCad board: {exc.message}", {"reason": exc.kind, **exc.details}) from exc


def _schematic_text(copy: Path) -> str:
    try:
        return sexp.decode(copy.read_bytes())
    except sexp.SexpError as exc:
        raise ToolFailure("input.invalid", f"not a readable KiCad schematic: {exc.message}", {"reason": exc.kind, **exc.details}) from exc


def _limit(value: int, low: int, high: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _bad(f"{name} must be a whole number from {low} to {high}", **{name: str(value)[:40]})
    return value


def _overrides(min_clearance: str, min_track_width: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, raw in (("clearance", min_clearance), ("track_width", min_track_width)):
        if raw is None or str(raw).strip() == "":
            continue
        value = parse_quantity(raw, name=f"min_{key}", dimension="length").to("millimeter").magnitude
        if not math.isfinite(value) or not 0.0 <= value <= 100.0:
            raise ToolFailure("input.out_of_range", f"min_{key} must be from 0 mm to 100 mm", {"param": f"min_{key}"})
        out[key] = float(value)
    return out


# --------------------------------------------------------------------------- pcb_info


def info(handle: JobHandle, *, board_path: str, max_listed: int, check_with_kicad: bool) -> Computed:
    max_listed = _limit(max_listed, 0, MAX_LISTED, "max_listed")
    copy = handle.input_file(board_path, param="board_path", extensions=(".kicad_pcb",), max_bytes=sexp.MAX_FILE_BYTES)
    data = boardread.read_board(_board_text(copy), max_list=max_listed)
    _check_finite(data, "result")
    warnings: list[str] = []
    outline = data["outline"]["extents_mm"]
    if outline is None:
        warnings.append("the board has no outline on Edge.Cuts: board_width and board_height are not given (KiCad's own DRC reports this as invalid_outline)")
    if data["parse_issues"]["count"]:
        warnings.append(f"{data['parse_issues']['count']} damaged element(s) were skipped; see data.parse_issues")
    if data["footprints"]["count"] > data["footprints"]["listed"]:
        warnings.append(f"only {data['footprints']['listed']} of {data['footprints']['count']} footprints are listed; raise max_listed (limit {MAX_LISTED})")
    if data["tracks"]["arcs"]:
        warnings.append("track arcs are measured as circular arcs through their three points")
    values = {
        "track_length_total": _mm(data["tracks"]["total_length_mm"]),
        "track_count": _count(data["tracks"]["count"]),
        "via_count": _count(data["vias"]["count"]),
        "footprint_count": _count(data["footprints"]["count"]),
        "net_count": _count(data["nets"]["count"]),
        "copper_layers": _count(data["layers"]["copper_count"]),
        "zone_count": _count(data["zones"]["count"]),
    }
    if outline is not None:
        values["board_width"] = _mm(outline["width"])
        values["board_height"] = _mm(outline["height"])
    data["conventions"] = {
        "coordinates": "millimetres as written in the file; x to the right, y DOWN (KiCad's board coordinates); angles in degrees, positive counter-clockwise on the screen",
        "track_length": "centre-line length of each segment; an arc is r * sweep",
        "outline": "extents of the centre lines of the shapes on Edge.Cuts; add half the line width for the copper-to-edge position",
    }
    if check_with_kicad:
        data["kicad_crosscheck"] = _crosscheck(handle, board_path, data, warnings)
    return Computed(values=values, primary="track_length_total", warnings=warnings, data=data)


def _crosscheck(handle: JobHandle, board_path: str, data: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    """Ask ``kicad-cli pcb export stats`` and ``pos`` for the numbers they give and compare them with this reader's."""
    try:
        prep = program.prepare(handle, board_path, kind="pcb", use_project_files=False)
    except ToolFailure as exc:
        if exc.family != "engine.unavailable":
            raise
        warnings.append("check_with_kicad was asked for but kicad-cli is not installed; the numbers above come from this tool's own reader only")
        return {"checked": False, "reason": exc.message[:300]}
    out = prep.work / "out"
    out.mkdir(exist_ok=True)
    program.run_cli(prep, ["pcb", "export", "stats", "--format", "json", "--units", "mm", "-o", out / "stats.json", prep.file])
    program.run_cli(prep, ["pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both", "-o", out / "positions.csv", prep.file])
    stats = program.read_json_report(out / "stats.json", "statistics report")
    rows: list[dict[str, Any]] = []

    def mmnum(text: Any) -> float | None:
        match = re.match(r"\s*(-?\d+(?:\.\d+)?)\s*mm\Z", text) if isinstance(text, str) else None
        return float(match.group(1)) if match else None

    def compare(what: str, ours: Any, theirs: Any, tolerance: float = 0.0) -> None:
        agree = ours is not None and theirs is not None and abs(ours - theirs) <= tolerance
        rows.append({"what": what, "this_reader": ours, "kicad": theirs, "agree": bool(agree)})

    board = stats.get("board", {}) if isinstance(stats.get("board"), dict) else {}
    pads = stats.get("pads", {}) if isinstance(stats.get("pads"), dict) else {}
    vias = stats.get("vias", {}) if isinstance(stats.get("vias"), dict) else {}
    outline = data["outline"]["extents_mm"]
    if outline is not None:
        compare("board_width_mm", outline["width"], mmnum(board.get("width")), 5e-3)
        compare("board_height_mm", outline["height"], mmnum(board.get("height")), 5e-3)
    by_type = data["pads"]["by_type"]
    compare("pads_smd", by_type.get("smd", 0), pads.get("smd"))
    compare("pads_through_hole", by_type.get("thru_hole", 0), pads.get("through_hole"))
    compare("pads_npth", by_type.get("np_thru_hole", 0), pads.get("npth"))
    compare("pads_connector", by_type.get("connect", 0), pads.get("connector"))
    compare("vias_through", data["vias"]["through"], vias.get("through"))
    compare("vias_blind", data["vias"]["blind"], vias.get("blind"))
    compare("vias_micro", data["vias"]["micro"], vias.get("micro"))
    if data["tracks"]["count"]:
        compare("track_width_min_mm", data["tracks"]["width_mm"]["min"], mmnum(board.get("min_track_width")), 6e-5)
    positions = Counter()
    ours = {fp["reference"]: fp for fp in data["footprints"]["list"]}
    try:
        reader = csv.DictReader(io.StringIO((out / "positions.csv").read_text(encoding="utf-8", errors="replace")))
        for row in reader:
            ref = row.get("Ref", "")
            fp = ours.get(ref)
            if fp is None:
                continue
            try:
                px, py = float(row["PosX"]), -float(row["PosY"])  # a position file has y pointing up
            except (KeyError, ValueError, TypeError):
                continue
            positions["checked"] += 1
            if abs(px - fp["x_mm"]) <= 1e-5 and abs(py - fp["y_mm"]) <= 1e-5:
                positions["agree"] += 1
    except OSError:
        pass
    rows.append({"what": "footprint_positions", "this_reader": positions["checked"], "kicad": positions["agree"], "agree": positions["checked"] == positions["agree"] and positions["checked"] > 0 or not ours})
    disagree = [r["what"] for r in rows if not r["agree"]]
    if disagree:
        warnings.append(f"this reader and kicad-cli disagree on: {', '.join(disagree)}")
    return {"checked": True, "kicad_version": prep.version, "comparisons": rows, "all_agree": not disagree}


# --------------------------------------------------------------------------- pcb_drc and pcb_erc


def _positions(items: Any, scale: float = 1.0, limit_items: int = 6) -> list[dict[str, Any]]:
    out = []
    for item in (items if isinstance(items, list) else [])[:limit_items]:
        if not isinstance(item, dict):
            continue
        pos = item.get("pos") if isinstance(item.get("pos"), dict) else {}
        x, y = program.mm_or_none(pos.get("x")), program.mm_or_none(pos.get("y"))
        out.append(
            {
                "description": program.short(item.get("description")),
                "x_mm": None if x is None else round(x * scale, 6),
                "y_mm": None if y is None else round(y * scale, 6),
                "uuid": program.short(item.get("uuid"), 64),
            }
        )
    return out


_REQUIRED_ACTUAL = re.compile(r"(\d+(?:\.\d+)?) mm; actual (\d+(?:\.\d+)?) mm")


def _violation(raw: Any, scale: float = 1.0) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    description = program.short(raw.get("description"))
    out: dict[str, Any] = {
        "type": program.short(raw.get("type"), 80),
        "severity": program.short(raw.get("severity"), 20),
        "description": description,
    }
    match = _REQUIRED_ACTUAL.search(description)  # "clearance 0.2000 mm; actual 0.1000 mm", English wording of KiCad 10
    if match:
        out["required_mm"], out["actual_mm"] = float(match.group(1)), float(match.group(2))
    out["items"] = _positions(raw.get("items"), scale)
    return out


def _group(violations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter((v["type"], v["severity"]) for v in violations)
    rows = [{"type": t, "severity": s, "count": c} for (t, s), c in counts.items()]
    rows.sort(key=lambda r: (SEVERITY_ORDER.get(r["severity"], 9), -r["count"], r["type"]))
    return rows


def _report_summary(violations: list[dict[str, Any]], max_violations: int) -> dict[str, Any]:
    by_severity = Counter(v["severity"] for v in violations)
    ordered = sorted(violations, key=lambda v: (SEVERITY_ORDER.get(v["severity"], 9), v["type"]))
    return {
        "total": len(violations),
        "errors": by_severity["error"],
        "warnings": by_severity["warning"],
        "exclusions": by_severity["exclusion"],
        "by_type": _group(violations),
        "listed": min(len(ordered), max_violations),
        "list": ordered[:max_violations],
    }


def drc(
    handle: JobHandle,
    *,
    board_path: str,
    use_project_files: bool,
    min_clearance: str,
    min_track_width: str,
    refill_zones: bool,
    max_violations: int,
) -> Computed:
    max_violations = _limit(max_violations, 0, MAX_LISTED, "max_violations")
    overrides = _overrides(min_clearance, min_track_width)
    prep = program.prepare(
        handle,
        board_path,
        kind="pcb",
        use_project_files=bool(use_project_files),
        overrides=overrides,
        validate=lambda copy: boardread.read_board(_board_text(copy), max_list=0),
    )
    board = prep.checked
    out = prep.work / "out"
    out.mkdir(exist_ok=True)
    args: list[Any] = ["pcb", "drc", "--format", "json", "--severity-all", "--units", "mm", "-o", out / "drc.json"]
    if refill_zones:
        args.append("--refill-zones")
    args.append(prep.file)
    record = program.run_cli(prep, args)
    report = program.read_json_report(out / "drc.json", "DRC report")
    violations = [v for v in (_violation(raw) for raw in (report.get("violations") or [])) if v]
    unconnected = [v for v in (_violation(raw) for raw in (report.get("unconnected_items") or [])) if v]
    parity = [v for v in (_violation(raw) for raw in (report.get("schematic_parity") or [])) if v]
    summary = _report_summary(violations, max_violations)
    listed_unconnected = unconnected[:max_violations]
    disabled = [{"key": program.short(c.get("key"), 60), "description": program.short(c.get("description"), 120)} for c in (report.get("ignored_checks") or []) if isinstance(c, dict)][:100]
    artifact = handle.write_bytes("drc_report.json", (out / "drc.json").read_bytes(), fmt="json")
    warnings = list(prep.warnings)
    if board["outline"]["extents_mm"] is None:
        warnings.append("the board has no outline on Edge.Cuts, so KiCad reports an invalid_outline error; that is not a problem with the rest of the board")
    if not refill_zones and board["zones"]["count"]:
        warnings.append("the board has zones and refill_zones is false: DRC uses the fills stored in the file, which may be stale or empty")
    if any(v["type"] == "lib_footprint_issues" for v in violations):
        warnings.append("lib_footprint_issues warnings mean a footprint's library is not visible to this run (library tables are deliberately not read); they say nothing about the board")
    if summary["listed"] < summary["total"]:
        warnings.append(f"only {summary['listed']} of {summary['total']} violations are listed; data.violations.by_type counts all of them")
    data = {
        "violations": summary,
        "unconnected": {"count": len(unconnected), "listed": len(listed_unconnected), "list": listed_unconnected},
        "schematic_parity": {"count": len(parity)},
        "rule_set": {**prep.rule_set, "checks_disabled": disabled},
        "kicad": {"version": program.short(report.get("kicad_version"), 40) or prep.version, "seconds": round(record.seconds, 3)},
        "board": {"format_version": board["file"]["format_version"], "copper_layers": board["layers"]["copper_count"], "tracks": board["tracks"]["count"], "footprints": board["footprints"]["count"]},
        "position_note": "x_mm and y_mm are the positions KiCad gives for each item involved (a track's start, a pad's centre, a via's centre); the marker of the violation itself is not in the report",
        "coordinates": "millimetres, x to the right, y down, as in the board file",
    }
    _check_finite(data, "result")
    values = {
        "error_count": _count(summary["errors"]),
        "warning_count": _count(summary["warnings"]),
        "unconnected_count": _count(len(unconnected)),
        "violation_count": _count(summary["total"]),
    }
    return Computed(values=values, primary="error_count", warnings=warnings, data=data, artifacts=[artifact])


def _calibrate(report_sheets: list[Any], symbols: dict[str, tuple[float, float]]) -> tuple[float, str]:
    """The factor that turns the positions in an ERC report into millimetres, found by comparing symbols the report names with the file.

    KiCad 10.0.6 was seen to print schematic positions 100 times smaller than the file's millimetres although it states
    ``"coordinate_units": "mm"``.  A report is trusted only as far as it agrees with the schematic: every ``Symbol <ref> [...]`` item
    is matched with the symbol of that reference in the file, and the ratio must be the same, and 1 or 100 (or 0.01 etc. of those), for all.
    """
    ratios: list[float] = []
    for sheet in report_sheets:
        for raw in sheet.get("violations", []) if isinstance(sheet, dict) else []:
            for item in raw.get("items", []) if isinstance(raw, dict) else []:
                text = item.get("description") if isinstance(item, dict) else None
                match = re.match(r"Symbol (\S+) \[", text) if isinstance(text, str) else None
                pos = item.get("pos") if isinstance(item, dict) and isinstance(item.get("pos"), dict) else {}
                if not match or match.group(1) not in symbols:
                    continue
                true_x, true_y = symbols[match.group(1)]
                x, y = pos.get("x"), pos.get("y")
                if isinstance(x, (int, float)) and isinstance(y, (int, float)) and abs(x) > 1e-9 and abs(true_x) > 1e-9:
                    ratios.append(true_x / x)
                    if abs(y) > 1e-9 and abs(true_y) > 1e-9:
                        ratios.append(true_y / y)
    if not ratios:
        return 1.0, "no symbol named in the report could be matched with the file, so positions are as KiCad printed them (unverified)"
    first = ratios[0]
    if any(abs(r - first) > 1e-3 * abs(first) for r in ratios):
        return 1.0, "positions in the report do not agree with the file's symbol positions by one factor, so they are as KiCad printed them (unverified)"
    factor = min((1.0, 10.0, 100.0, 1000.0, 0.1, 0.01), key=lambda f: abs(first - f))
    if abs(first - factor) > 1e-3 * factor:
        return 1.0, f"positions in the report differ from the file by the odd factor {first:.4g}, so they are as KiCad printed them (unverified)"
    note = f"positions matched against {len(ratios)} coordinate(s) of symbols in the file; factor {factor:g} applied to the report's numbers"
    return factor, note


def erc(handle: JobHandle, *, schematic_path: str, use_project_files: bool, max_violations: int) -> Computed:
    max_violations = _limit(max_violations, 0, MAX_LISTED, "max_violations")
    prep = program.prepare(
        handle,
        schematic_path,
        kind="sch",
        use_project_files=bool(use_project_files),
        param="schematic_path",
        validate=lambda copy: schread.read_schematic(_schematic_text(copy)),
    )
    sch = prep.checked
    out = prep.work / "out"
    out.mkdir(exist_ok=True)
    record = program.run_cli(prep, ["sch", "erc", "--format", "json", "--severity-all", "--units", "mm", "-o", out / "erc.json", prep.file])
    report = program.read_json_report(out / "erc.json", "ERC report")
    sheets = [s for s in (report.get("sheets") or []) if isinstance(s, dict)]
    factor, calibration = _calibrate(sheets, sch["symbols"])
    violations: list[dict[str, Any]] = []
    for sheet in sheets:
        for raw in sheet.get("violations") or []:
            v = _violation(raw, factor)
            if v:
                v["sheet"] = program.short(sheet.get("path"), 120)
                violations.append(v)
    summary = _report_summary(violations, max_violations)
    disabled = [{"key": program.short(c.get("key"), 60), "description": program.short(c.get("description"), 120)} for c in (report.get("ignored_checks") or []) if isinstance(c, dict)][:100]
    artifact = handle.write_bytes("erc_report.json", (out / "erc.json").read_bytes(), fmt="json")
    warnings = list(prep.warnings)
    if sch["sheets"]:
        warnings.append(
            f"the schematic holds {len(sch['sheets'])} hierarchical sheet block(s); only this one file is given to KiCad, "
            "so the sheets in other files are not checked (their files are not copied)"
        )
    if any(v["type"] == "lib_symbol_issues" for v in violations):
        warnings.append("lib_symbol_issues warnings mean a symbol's library is not visible to this run (library tables are deliberately not read); the symbol copy embedded in the schematic was used")
    if summary["listed"] < summary["total"]:
        warnings.append(f"only {summary['listed']} of {summary['total']} violations are listed; data.violations.by_type counts all of them")
    data = {
        "violations": summary,
        "rule_set": {**prep.rule_set, "checks_disabled": disabled},
        "kicad": {"version": program.short(report.get("kicad_version"), 40) or prep.version, "seconds": round(record.seconds, 3)},
        "schematic": {"format_version": sch["version"], "symbols": len(sch["symbols"]), "hierarchical_sheets": len(sch["sheets"])},
        "position_calibration": calibration,
        "coordinates": "millimetres, x to the right, y down, as in the schematic file",
    }
    _check_finite(data, "result")
    values = {
        "error_count": _count(summary["errors"]),
        "warning_count": _count(summary["warnings"]),
        "violation_count": _count(summary["total"]),
    }
    return Computed(values=values, primary="error_count", warnings=warnings, data=data, artifacts=[artifact])


# --------------------------------------------------------------------------- pcb_export


def _layers(raw: str, board: dict[str, Any], kind: str) -> tuple[list[str], list[str]]:
    """``(layers, warnings)``: the layers to plot, each one checked against the names in the board's own layer table."""
    available = list(board["layers"]["all"])
    if raw is None or raw.strip() == "":
        if kind == "gerbers":
            chosen = [n for n in available if PLOT_LAYER.match(n)]
        else:
            chosen = [*board["layers"]["copper"], *(["Edge.Cuts"] if "Edge.Cuts" in available else [])]
        return chosen, []
    names = [part.strip() for part in raw.split(",")]
    if len(names) > MAX_LAYERS:
        raise _bad(f"layers lists {len(names)} names; the limit is {MAX_LAYERS}")
    seen: list[str] = []
    for name in names:
        if not LAYER_NAME.match(name):
            raise _bad(f"layer name {name[:40]!r} is not a KiCad layer name (letters, digits, '_' and '.' only)", layer=name[:40], available=available[:60])
        if name not in available:
            raise _bad(f"the board has no layer named {name!r}; its layers are {available}", layer=name, available=available[:60])
        if name not in seen:
            seen.append(name)
    return seen, []


def export(handle: JobHandle, *, board_path: str, export: str, layers: str) -> Computed:
    kind = (export or "").strip().lower()
    if kind not in EXPORTS:
        raise _bad(f"export must be one of {list(EXPORTS)}", export=str(export)[:40])

    def check(copy: Path) -> tuple[dict[str, Any], list[str]]:
        board = boardread.read_board(_board_text(copy), max_list=0)
        chosen: list[str] = []
        if kind in ("gerbers", "pdf", "svg"):
            chosen, _ = _layers(layers, board, kind)
            if not chosen:
                raise _bad("the board has no layer to plot")
        return board, chosen

    prep = program.prepare(handle, board_path, kind="pcb", use_project_files=False, validate=check)
    board, plot_layers = prep.checked
    warnings = list(prep.warnings)
    if kind not in ("gerbers", "pdf", "svg") and layers and layers.strip():
        warnings.append(f"layers is ignored for the {kind} export")
    out = prep.work / "out"
    out.mkdir(exist_ok=True)
    base: list[Any] = ["pcb", "export"]
    if kind == "gerbers":
        args = [*base, "gerbers", "-o", out, "--layers", ",".join(plot_layers), prep.file]
    elif kind == "drill":
        args = [*base, "drill", "-o", out, "--format", "excellon", "--excellon-units", "mm", prep.file]
    elif kind == "pos":
        args = [*base, "pos", "--format", "csv", "--units", "mm", "--side", "both", "-o", out / "positions.csv", prep.file]
    elif kind == "pdf":
        args = [*base, "pdf", "--mode-single", "-o", out / "board.pdf", "--layers", ",".join(plot_layers), prep.file]
    else:
        args = [*base, "svg", "--mode-single", "--page-size-mode", "2", "-o", out / "board.svg", "--layers", ",".join(plot_layers), prep.file]
    record = program.run_cli(prep, args)
    produced = sorted((f for f in record.files if f.name.startswith("out/")), key=lambda f: f.name)
    if not produced:
        raise ToolFailure("engine.failure", f"kicad-cli finished without producing any {kind} file", {"stdout_tail": record.stdout_tail})
    formats = {".gtl": "gerber", ".gbl": "gerber", ".drl": "excellon", ".csv": "csv", ".pdf": "pdf", ".svg": "svg", ".gbrjob": "json", ".rpt": "text"}
    files: list[dict[str, Any]] = []
    artifacts = []
    for f in produced:
        name = f.name[len("out/") :]
        suffix = Path(name).suffix.lower()
        fmt = formats.get(suffix) or ("gerber" if re.fullmatch(r"\.g[0-9a-z]{1,2}", suffix) else suffix.lstrip(".") or "file")
        payload = (prep.work / f.name).read_bytes()
        artifact = handle.write_bytes(name, payload, fmt=fmt)
        artifacts.append(artifact)
        files.append({"name": name, "bytes": f.bytes, "sha256": f.sha256, "format": fmt})
    if kind == "gerbers":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for f in files:
                info_ = zipfile.ZipInfo(f["name"], date_time=(1980, 1, 1, 0, 0, 0))
                info_.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info_, (prep.work / "out" / f["name"]).read_bytes())
        payload = buffer.getvalue()
        artifacts.append(handle.write_bytes("gerbers.zip", payload, fmt="zip"))
        files.append({"name": "gerbers.zip", "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "format": "zip"})
        warnings.append("gerbers.zip holds the plotted layers and the .gbrjob job file; drill files are a separate export (export='drill')")
    data = {
        "export": kind,
        "layers": plot_layers,
        "files": files,
        "kicad": {"version": prep.version, "seconds": round(record.seconds, 3)},
        "output": "gerbers use KiCad's default plot options with X2 attributes on; positions are millimetres with y UP (a position file's convention); pdf and svg are one page with the chosen layers on top of each other",
    }
    values = {"file_count": _count(len(produced))}
    return Computed(values=values, primary="file_count", warnings=warnings, data=data, artifacts=artifacts)


__all__ = ["drc", "erc", "export", "info"]
