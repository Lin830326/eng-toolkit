"""Starting ``kicad-cli`` on a copy of the caller's file, in a job directory that holds nothing else.

What this module decides, and why (the long version is ``docs/pcb/design.md``):

* The command line is built here from a fixed vocabulary.  Caller text never reaches it except as a value checked against a
  list read from the board (a layer name), a fixed enumeration, or a number turned into text by us.  The copied board has a
  fixed name (``board.kicad_pcb``), so even a hostile file name cannot reach the command line.
* ``kicad-cli`` is given a **copy** of the file (made by ``JobHandle.input_file``: opened once, checked on the open file, size
  capped).  The layer under :mod:`eng_toolkit.external` points ``APPDATA``/``HOME`` and the temp directories into the job
  directory, so the user's KiCad settings, library tables and plugins are not visible to the program.
* Project files next to the original are **not copied as they are**.  ``<name>.kicad_pro`` is read as JSON and only an allow-list of
  numbers, severities and net class settings is carried over into a new project file we write; ``<name>.kicad_dru`` (custom
  rules) is carried over after it is checked to contain nothing but ``(version ...)`` and ``(rule ...)`` lists and to be small.
  Library tables (``fp-lib-table``, ``sym-lib-table``), ``.kicad_prl``, drawing sheets and 3D models are never copied.
* The program's language is fixed to English through the settings file in the job's own settings directory, so descriptions in its
  reports do not depend on the server's locale.  Results never depend on the text: they use the ``type`` and ``severity`` keys.
"""

from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from eng_toolkit import external
from eng_toolkit.domains.pcb import sexp
from eng_toolkit.errors import ToolFailure
from eng_toolkit.external_catalog import KICAD_CLI
from eng_toolkit.runner import JobHandle

BOARD_NAME = "board.kicad_pcb"
SCHEMATIC_NAME = "board.kicad_sch"
PROJECT_NAME = "board.kicad_pro"
RULES_NAME = "board.kicad_dru"
MAX_BOARD_BYTES = sexp.MAX_FILE_BYTES
MAX_PROJECT_BYTES = 5_000_000
MAX_RULES_BYTES = 256_000
MAX_REPORT_BYTES = 40_000_000
DEFAULT_TIMEOUT_S = 180.0
DEFAULT_MEMORY_MB = 4096
SEVERITIES = ("error", "warning", "ignore")

_BOARD_RULE_FLOATS = (
    "max_error",
    "min_clearance",
    "min_connection",
    "min_copper_edge_clearance",
    "min_hole_clearance",
    "min_hole_to_hole",
    "min_microvia_diameter",
    "min_microvia_drill",
    "min_silk_clearance",
    "min_text_height",
    "min_text_thickness",
    "min_through_hole_diameter",
    "min_track_width",
    "min_via_annular_width",
    "min_via_diameter",
    "solder_mask_to_copper_clearance",
)
_NETCLASS_FLOATS = (
    "clearance",
    "track_width",
    "via_diameter",
    "via_drill",
    "microvia_diameter",
    "microvia_drill",
    "diff_pair_gap",
    "diff_pair_via_gap",
    "diff_pair_width",
)
_KEY = re.compile(r"[a-z0-9_]{1,60}\Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_VERSION = re.compile(r"(\d+)\.(\d+)")


# --------------------------------------------------------------------------- the job directory


def job_dir(handle: JobHandle) -> Path:
    """The directory of this call's job (the handle keeps it private; every domain that starts a program needs it)."""
    return Path(handle._job.dir)  # noqa: SLF001


def find_kicad() -> external.Located:
    located, why = external.locate(KICAD_CLI)
    if located is None:
        raise external.unavailable(KICAD_CLI, why)
    return located


_version_cache: dict[str, str | None] = {}


def kicad_version(located: external.Located) -> str | None:
    if located.path not in _version_cache:
        _version_cache[located.path] = external.version(KICAD_CLI, located)
    return _version_cache[located.path]


def write_language_setting(work: Path, version: str | None) -> None:
    """``kicad_common.json`` with English as the language, in every place KiCad may look for its settings inside ``work/home``."""
    match = _VERSION.match(version or "")
    if not match:
        return
    name = f"{match.group(1)}.{match.group(2)}"
    text = json.dumps({"system": {"language": "English"}}) + "\n"
    for base in (Path("AppData") / "Roaming", Path(".config"), Path("Library") / "Preferences"):
        target = work / "home" / base / "kicad" / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "kicad_common.json").write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- sibling project files


def _finite(value: Any, low: float, high: float) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or not low <= value <= high:
        return None
    return value


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or len(value) > limit or _CONTROL.search(value):
        return None
    return value


def _severities(raw: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for key, value in list(raw.items())[:200]:
            if isinstance(key, str) and _KEY.match(key) and value in SEVERITIES:
                out[key] = value
    return out


def _strings(raw: Any, limit: int = 2000) -> list[str]:
    out: list[str] = []
    if isinstance(raw, list):
        for item in raw[:limit]:
            text = _text(item, 1000)
            if text is not None:
                out.append(text)
    return out


def sanitize_project(data: Any, *, schematic: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(project, summary)``: a new project dictionary holding only what is on the allow-list, and a description of what was carried.

    Allowed for a board: ``board.design_settings.rules`` (the numbers in :data:`_BOARD_RULE_FLOATS`),
    ``board.design_settings.rule_severities``, ``board.design_settings.drc_exclusions`` (strings), ``net_settings.classes``
    (name and the numbers in :data:`_NETCLASS_FLOATS`) and ``net_settings.netclass_patterns``.  Allowed for a schematic: ``erc.rule_severities``,
    ``erc.pin_map`` (a 12 by 12 table of 0, 1 or 2) and ``erc.erc_exclusions``.  Everything else (paths, library lists, text variables,
    drawing sheet names, board and schematic appearance) is left out.
    """
    if not isinstance(data, dict):
        raise ToolFailure("input.invalid", "the project file is not a JSON object", {})
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    version = _finite(meta.get("version"), 0, 1000)
    project: dict[str, Any] = {"meta": {"filename": PROJECT_NAME, "version": int(version) if version is not None else 3}}
    summary: dict[str, Any] = {"used": True, "rules": {}, "severities": {}, "net_classes": [], "exclusions": 0}
    if schematic:
        erc = data.get("erc") if isinstance(data.get("erc"), dict) else {}
        out: dict[str, Any] = {"meta": {"version": 0}}
        severities = _severities(erc.get("rule_severities"))
        if severities:
            out["rule_severities"] = severities
        pin_map = erc.get("pin_map")
        if isinstance(pin_map, list) and len(pin_map) == 12 and all(isinstance(r, list) and len(r) == 12 and all(c in (0, 1, 2) and not isinstance(c, bool) for c in r) for r in pin_map):
            out["pin_map"] = pin_map
            summary["pin_map"] = "carried over"
        exclusions = _strings(erc.get("erc_exclusions"))
        if exclusions:
            out["erc_exclusions"] = exclusions
        project["erc"] = out
        summary["severities"] = severities
        summary["exclusions"] = len(exclusions)
    else:
        settings = ((data.get("board") or {}).get("design_settings") or {}) if isinstance(data.get("board"), dict) else {}
        settings = settings if isinstance(settings, dict) else {}
        design: dict[str, Any] = {}
        rules_in = settings.get("rules") if isinstance(settings.get("rules"), dict) else {}
        rules: dict[str, Any] = {}
        for key in _BOARD_RULE_FLOATS:
            value = _finite(rules_in.get(key), 0.0, 1000.0)
            if value is not None:
                rules[key] = value
        spokes = _finite(rules_in.get("min_resolved_spokes"), 0, 20)
        if spokes is not None:
            rules["min_resolved_spokes"] = int(spokes)
        if rules:
            design["rules"] = rules
        severities = _severities(settings.get("rule_severities"))
        if severities:
            design["rule_severities"] = severities
        exclusions = _strings(settings.get("drc_exclusions"))
        if exclusions:
            design["drc_exclusions"] = exclusions
        if design:
            project["board"] = {"design_settings": design}
        net_settings = data.get("net_settings") if isinstance(data.get("net_settings"), dict) else {}
        classes = []
        for entry in (net_settings.get("classes") if isinstance(net_settings.get("classes"), list) else [])[:200]:
            if not isinstance(entry, dict):
                continue
            name = _text(entry.get("name"), 100)
            if not name:
                continue
            item: dict[str, Any] = {"name": name}
            for key in _NETCLASS_FLOATS:
                value = _finite(entry.get(key), 0.0, 1000.0)
                if value is not None:
                    item[key] = value
            classes.append(item)
        patterns = []
        for entry in (net_settings.get("netclass_patterns") if isinstance(net_settings.get("netclass_patterns"), list) else [])[:1000]:
            if isinstance(entry, dict):
                netclass, pattern = _text(entry.get("netclass"), 100), _text(entry.get("pattern"), 300)
                if netclass and pattern:
                    patterns.append({"netclass": netclass, "pattern": pattern})
        if classes or patterns:
            project["net_settings"] = {"classes": classes, "meta": {"version": 4}, "netclass_patterns": patterns}
        summary.update(rules=rules, severities=severities, net_classes=[c["name"] for c in classes], exclusions=len(exclusions), netclass_patterns=len(patterns))
    summary["left_out"] = sorted(k for k in data if k not in ("meta", "erc" if schematic else "board", "net_settings") and isinstance(k, str))[:30]
    return project, summary


def check_rules_text(text: str) -> int:
    """The number of ``(rule ...)`` lists in a custom rules file; only ``(version ...)`` and ``(rule ...)`` are allowed at the top level."""
    rules = 0
    # a rules file is a sequence of lists, not one root list: parse each top-level list on its own
    for part in _top_level_lists(text):
        try:
            nodes = list(sexp.top_level(part))
        except sexp.SexpError as exc:
            raise ToolFailure("input.invalid", f"the custom rules file is not valid: {exc.message}", {"reason": exc.kind}) from exc
        name = nodes[0][0] if nodes and nodes[0] else ""
        if name == "rule":
            rules += 1
        elif name != "version":
            raise ToolFailure("input.invalid", f"the custom rules file holds a top-level ({name[:30]} ...) list; only (version ...) and (rule ...) are accepted", {"element": name[:30]})
    return rules


def _top_level_lists(text: str) -> list[str]:
    """Split a text into its top-level parenthesised lists (quotes respected); text between lists must be blank or a ``#`` comment."""
    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif ch == "#" and depth == 0:
            while i < n and text[i] != "\n":
                i += 1
        elif ch == "(":
            if depth == 0:
                start = i
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                raise ToolFailure("input.invalid", "the custom rules file has an unbalanced ')'", {})
            if depth == 0:
                parts.append(text[start : i + 1])
        elif depth == 0 and not ch.isspace():
            raise ToolFailure("input.invalid", "the custom rules file has text outside its lists", {})
        i += 1
    if depth:
        raise ToolFailure("input.invalid", "the custom rules file has an unclosed '('", {})
    return parts


# --------------------------------------------------------------------------- staging


@dataclass
class Prepared:
    work: Path
    file: Path
    located: external.Located
    version: str | None
    rule_set: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    checked: Any = None  # what the caller's own reader found in the file before KiCad was started


def _sibling(handle: JobHandle, raw: str, suffix: str, param: str, max_bytes: int) -> Path | None:
    """A copy of ``<stem><suffix>`` next to the caller's file, or ``None`` when there is none or it cannot be read."""
    original = Path(raw).expanduser()
    candidate = original.with_suffix(suffix)
    if candidate == original or not candidate.is_file():
        return None
    try:
        return handle.input_file(str(candidate), param=param, extensions=(suffix,), max_bytes=max_bytes)
    except ToolFailure as exc:
        if exc.family in ("input.file_not_found", "input.path_not_allowed", "input.too_large"):
            return None
        raise


def format_mm(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def prepare(
    handle: JobHandle,
    raw_path: str,
    *,
    kind: str,
    use_project_files: bool,
    overrides: dict[str, float] | None = None,
    param: str = "board_path",
    validate: Callable[[Path], Any] | None = None,
) -> Prepared:
    """Copy the caller's file and, if wanted, the allow-listed parts of its project files into ``<job>/work`` and describe the rule set.

    ``kind`` is ``'pcb'`` or ``'sch'``.  ``overrides`` maps ``'clearance'`` and ``'track_width'`` to millimetres (pcb only); they become
    custom rules written by us.  Nothing is started here.
    """
    schematic = kind == "sch"
    suffix, name = (".kicad_sch", SCHEMATIC_NAME) if schematic else (".kicad_pcb", BOARD_NAME)
    copy = handle.input_file(raw_path, param=param, extensions=(suffix,), max_bytes=MAX_BOARD_BYTES)
    checked = validate(copy) if validate is not None else None  # our reader first: a file that is not a board never reaches KiCad
    located = find_kicad()
    work = job_dir(handle) / "work"
    work.mkdir(exist_ok=True)
    staged = work / name
    shutil.copyfile(copy, staged)
    version = kicad_version(located)
    write_language_setting(work, version)
    warnings: list[str] = []
    rule_set: dict[str, Any] = {
        "source": "kicad_defaults",
        "project_file": {"used": False},
        "custom_rules_file": {"used": False},
        "overrides_mm": dict(overrides or {}),
    }
    if use_project_files:
        pro = _sibling(handle, raw_path, ".kicad_pro", "project file", MAX_PROJECT_BYTES)
        if pro is not None:
            try:
                data = json.loads(pro.read_bytes().decode("utf-8"), parse_constant=_reject_constant)
                project, summary = sanitize_project(data, schematic=schematic)
                (work / PROJECT_NAME).write_text(json.dumps(project, indent=2) + "\n", encoding="utf-8")
                rule_set["project_file"] = summary
                rule_set["source"] = "project_file"
            except (ValueError, UnicodeDecodeError, RecursionError) as exc:
                warnings.append(f"the project file next to the board was not used: {str(exc)[:120]}")
                rule_set["project_file"] = {"used": False, "error": str(exc)[:120]}
    rules_text = ""
    if use_project_files and not schematic:
        dru = _sibling(handle, raw_path, ".kicad_dru", "custom rules file", MAX_RULES_BYTES)
        if dru is not None:
            try:
                rules_text = sexp.decode(dru.read_bytes())
                count = check_rules_text(rules_text)
                rule_set["custom_rules_file"] = {"used": True, "rules": count, "bytes": len(rules_text.encode("utf-8"))}
                rule_set["source"] = "project_file"
            except (ToolFailure, sexp.SexpError) as exc:
                rules_text = ""
                message = exc.message if isinstance(exc, (ToolFailure, sexp.SexpError)) else str(exc)
                warnings.append(f"the custom rules file next to the board was not used: {message[:120]}")
                rule_set["custom_rules_file"] = {"used": False, "error": message[:120]}
    extra = []
    if overrides and not schematic:
        for key in ("clearance", "track_width"):
            if key in overrides:
                extra.append(f'(rule "toolkit_min_{key}"\n\t(constraint {key} (min {format_mm(overrides[key])}mm)))\n')
    if rules_text or extra:
        body = rules_text if rules_text.strip() else "(version 1)\n"
        (work / RULES_NAME).write_text(body.rstrip("\n") + "\n" + "".join(extra), encoding="utf-8")
        if extra:
            rule_set["source"] = "project_file" if rule_set["source"] == "project_file" else "overrides"
    if rule_set["source"] == "kicad_defaults":
        rule_set["note"] = f"no project rules: KiCad {version or '(version unknown)'} built-in defaults, which depend on its version"
    return Prepared(work=work, file=staged, located=located, version=version, rule_set=rule_set, warnings=warnings, checked=checked)


def _reject_constant(token: str) -> Any:
    raise ValueError(f"the project file holds {token}, which is not JSON")


# --------------------------------------------------------------------------- running


def run_cli(prep: Prepared, args: list[Any], *, timeout_s: float = DEFAULT_TIMEOUT_S, memory_mb: int = DEFAULT_MEMORY_MB) -> external.Record:
    """Start ``kicad-cli`` with ``args`` (already built from fixed words and validated values) in the job's ``work`` directory."""
    return external.run(KICAD_CLI, args, workdir=prep.work, located=prep.located, timeout_s=timeout_s, memory_mb=memory_mb)


def read_json_report(path: Path, what: str) -> dict[str, Any]:
    """The JSON report a KiCad run wrote: size-capped, NaN and Infinity refused, an object."""
    if not path.is_file():
        raise ToolFailure("engine.failure", f"kicad-cli finished without writing the {what}", {})
    size = path.stat().st_size
    if size > MAX_REPORT_BYTES:
        raise ToolFailure("engine.resource_limit", f"the {what} is {size} bytes, over the {MAX_REPORT_BYTES} byte limit", {"bytes": size})
    try:
        data = json.loads(path.read_bytes().decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ToolFailure("engine.failure", f"the {what} written by kicad-cli is not valid JSON: {str(exc)[:120]}", {}) from exc
    if not isinstance(data, dict):
        raise ToolFailure("engine.failure", f"the {what} written by kicad-cli is not a JSON object", {})
    return data


def mm_or_none(value: Any, limit: float = 1.0e6) -> float | None:
    """A coordinate from a KiCad report as a finite float within ``limit``, else ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and abs(value) <= limit else None


def short(text: Any, limit: int = 300) -> str:
    if not isinstance(text, str):
        return ""
    text = _CONTROL.sub(" ", text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = [
    "BOARD_NAME",
    "Prepared",
    "SCHEMATIC_NAME",
    "check_rules_text",
    "find_kicad",
    "format_mm",
    "job_dir",
    "kicad_version",
    "mm_or_none",
    "prepare",
    "read_json_report",
    "run_cli",
    "sanitize_project",
    "short",
]
