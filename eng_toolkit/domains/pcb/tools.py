"""``pcb`` sub-server: KiCad boards and schematics.

``pcb_info`` reads a ``.kicad_pcb`` with our own parser (no KiCad needed); ``pcb_drc``, ``pcb_erc`` and ``pcb_export`` start the
``kicad-cli`` program the user installed, on a copy of the file, with a command line built here from fixed words
(``docs/pcb/design.md``).  Nothing in a file is ever executed.
"""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.pcb import compute
from eng_toolkit.runner import run_tool

ENGINE_INFO = "eng_toolkit.pcb.info"
ENGINE_DRC = "eng_toolkit.pcb.drc"
ENGINE_ERC = "eng_toolkit.pcb.erc"
ENGINE_EXPORT = "eng_toolkit.pcb.export"
TOOL_NAMES: tuple[str, ...] = ("info", "drc", "erc", "export")

server = FastMCP(
    "pcb",
    instructions=(
        "KiCad boards (.kicad_pcb) and schematics (.kicad_sch). pcb_info reads a board without KiCad; pcb_drc, pcb_erc and pcb_export "
        "need the user's own kicad-cli. Lengths are millimetres. Results are unreviewed checks and must be reviewed by a qualified engineer."
    ),
)

STANDING = (
    "A rule check lists what the rules the run used can see; passing it does not prove the design is manufacturable or correct. "
    "A qualified engineer must review the board or schematic.",
)

INFO_DESCRIPTION = (
    "Read a KiCad board (.kicad_pcb, at most 50 MB) with the toolkit's own parser; KiCad is not needed. Returns values (mm and counts): "
    "track_length_total, track_count, via_count, footprint_count, net_count, copper_layers, zone_count, and board_width and board_height "
    "from the Edge.Cuts outline (centre lines); data: format version, layer names, nets with their pads/tracks/vias/length, footprints "
    "(reference, value, x/y in mm with y pointing DOWN, angle, side, pads), pad types and drills, tracks by layer (count, length, widths), "
    "vias (kinds, drills, sizes), zones (name, net, layers, outline area), parse_issues (damaged elements that were skipped) and "
    "ignored_elements. Files from KiCad 5 to 10 are read. Refuses a file that is not a KiCad board, is nested deeper than 64 levels, "
    "holds more than 1,000,000 elements, or has a coordinate beyond 10 m. Set check_with_kicad to compare its counts, outline and footprint "
    "positions with kicad-cli's own statistics and position file (needs kicad-cli)."
)

DRC_DESCRIPTION = (
    "Run KiCad's design rule check (kicad-cli pcb drc, user-installed) on a copy of a .kicad_pcb. Values: error_count, warning_count, "
    "unconnected_count, violation_count. data.violations lists them by type and severity (type is a KiCad key such as clearance, "
    "track_width, invalid_outline, via_dangling) with the description and the position in mm of each item involved; data.unconnected "
    "lists missing connections; data.rule_set says which rules were used: KiCad's built-in defaults, or an allow-listed copy of the "
    "numbers in the <name>.kicad_pro and the <name>.kicad_dru next to the board (use_project_files=false ignores them), plus the "
    "min_clearance / min_track_width you pass (e.g. '0.15 mm'). A board with no Edge.Cuts outline always gets one invalid_outline error. "
    "The raw KiCad report is an artifact. Footprint libraries are not visible to the run, so lib_footprint_issues warnings can appear: they are split "
    "out in data.violations.library_warnings (and values.library_warning_count) from the findings about the board. kicad-cli stops listing a kind of violation at "
    "499 (clearance) or 199 (track_width and others): a count equal to that is a lower bound, flagged in data.violations.at_kicad_limit and a warning. "
    "Refuses non-KiCad files, absurd coordinates and deep nesting before KiCad starts; needs kicad-cli."
)

ERC_DESCRIPTION = (
    "Run KiCad's electrical rules check (kicad-cli sch erc, user-installed) on a copy of one .kicad_sch file. Values: error_count, "
    "warning_count, violation_count. data.violations lists them by type (pin_not_connected, pin_to_pin, lib_symbol_issues ...) and "
    "severity with the description and position in mm of each item; data.rule_set says which rules were used (KiCad's defaults, or the "
    "allow-listed severities and pin map from the <name>.kicad_pro next to the file; use_project_files=false ignores it). Only the one "
    "file is checked: sub-sheets in other files are not copied. Symbol libraries are not visible, so lib_symbol_issues warnings appear "
    "for library symbols (the copy embedded in the file is used); they are split out in data.violations.library_warnings from the real findings. "
    "Raw report is an artifact. Needs kicad-cli."
)

EXPORT_DESCRIPTION = (
    "Export from a copy of a .kicad_pcb with kicad-cli (user-installed). export is one of 'gerbers' (one file per layer, the job file "
    "and the Excellon drill file, all named after the board, plus <board>-gerbers.zip holding all of them: one upload for a fabricator), "
    "'drill' (Excellon, mm), 'pos' (CSV of footprint positions, mm, y pointing UP, both sides), 'pdf' or 'svg' (the board fills the page: "
    "svg is cropped to the board outline, pdf autoscaled; the chosen layers on top of each other, or one file per layer with "
    "per_layer=true, Edge.Cuts on each). layers is a comma-separated list of the board's layer names, e.g. 'F.Cu,Edge.Cuts' (names are "
    "checked against the board's own layer table; empty = all copper layers and Edge.Cuts, or for gerbers every copper, mask, silk, paste "
    "and Edge.Cuts layer). Returns file_count, and data.files with name, bytes and sha256 of every file; the files are artifacts. "
    "No 3D, STEP, VRML or PNG export, and nothing is edited. Needs kicad-cli."
)


@server.tool(name="info", description=INFO_DESCRIPTION)
def info(
    board_path: Annotated[str, Field(description="Path to a .kicad_pcb file, at most 50 MB")],
    max_listed: Annotated[int, Field(description="How many footprints, nets and zones to list in data (0 to 1000); counts are always complete")] = 200,
    check_with_kicad: Annotated[bool, Field(description="True: also compare counts, outline and footprint positions with kicad-cli's own export (needs kicad-cli)")] = False,
) -> ToolResult:
    inputs = {"board_path": board_path, "max_listed": max_listed, "check_with_kicad": check_with_kicad}
    return run_tool(
        tool_name="pcb_info",
        engine=ENGINE_INFO,
        version=__version__,
        inputs=inputs,
        compute=lambda job: compute.info(job, board_path=board_path, max_listed=max_listed, check_with_kicad=check_with_kicad),
        standing_warnings=("Counts and lengths are read from the file; whether the board is right is for a qualified engineer to review.",),
    )


@server.tool(name="drc", description=DRC_DESCRIPTION)
def drc(
    board_path: Annotated[str, Field(description="Path to a .kicad_pcb file, at most 50 MB")],
    use_project_files: Annotated[bool, Field(description="True: use the allow-listed rules of <name>.kicad_pro and <name>.kicad_dru next to the board if they exist; false: KiCad defaults only")] = True,
    min_clearance: Annotated[str, Field(description="Optional minimum copper clearance with unit, 0 to 100 mm, e.g. '0.2 mm'; empty = the rule set's own")] = "",
    min_track_width: Annotated[str, Field(description="Optional minimum track width with unit, 0 to 100 mm, e.g. '0.15 mm'; empty = the rule set's own")] = "",
    refill_zones: Annotated[bool, Field(description="True: refill zones before checking (slower); false: use the fills stored in the file")] = False,
    max_violations: Annotated[int, Field(description="How many violations to list (0 to 1000); counts by type are complete up to kicad-cli's own limit (499 clearance, 199 others)")] = 100,
) -> ToolResult:
    inputs = {
        "board_path": board_path,
        "use_project_files": use_project_files,
        "min_clearance": min_clearance,
        "min_track_width": min_track_width,
        "refill_zones": refill_zones,
        "max_violations": max_violations,
    }
    return run_tool(
        tool_name="pcb_drc",
        engine=ENGINE_DRC,
        version=__version__,
        inputs=inputs,
        compute=lambda job: compute.drc(
            job,
            board_path=board_path,
            use_project_files=use_project_files,
            min_clearance=min_clearance,
            min_track_width=min_track_width,
            refill_zones=refill_zones,
            max_violations=max_violations,
        ),
        standing_warnings=STANDING,
    )


@server.tool(name="erc", description=ERC_DESCRIPTION)
def erc(
    schematic_path: Annotated[str, Field(description="Path to a .kicad_sch file, at most 50 MB")],
    use_project_files: Annotated[bool, Field(description="True: use the allow-listed ERC settings of <name>.kicad_pro next to the file if it exists; false: KiCad defaults only")] = True,
    max_violations: Annotated[int, Field(description="How many violations to list (0 to 1000); the counts by type are always complete")] = 100,
) -> ToolResult:
    inputs = {"schematic_path": schematic_path, "use_project_files": use_project_files, "max_violations": max_violations}
    return run_tool(
        tool_name="pcb_erc",
        engine=ENGINE_ERC,
        version=__version__,
        inputs=inputs,
        compute=lambda job: compute.erc(job, schematic_path=schematic_path, use_project_files=use_project_files, max_violations=max_violations),
        standing_warnings=STANDING,
    )


@server.tool(name="export", description=EXPORT_DESCRIPTION)
def export(
    board_path: Annotated[str, Field(description="Path to a .kicad_pcb file, at most 50 MB")],
    export: Annotated[str, Field(description="'gerbers', 'drill', 'pos', 'pdf' or 'svg'")],
    layers: Annotated[str, Field(description="Comma-separated layer names of this board for gerbers, pdf and svg, e.g. 'F.Cu,B.Cu,Edge.Cuts'; empty = default set")] = "",
    per_layer: Annotated[bool, Field(description="pdf and svg: one file per layer (Edge.Cuts drawn on each) instead of all layers overlaid on one page")] = False,
) -> ToolResult:
    inputs = {"board_path": board_path, "export": export, "layers": layers, "per_layer": per_layer}
    return run_tool(
        tool_name="pcb_export",
        engine=ENGINE_EXPORT,
        version=__version__,
        inputs=inputs,
        compute=lambda job: compute.export(job, board_path=board_path, export=export, layers=layers, per_layer=per_layer),
        standing_warnings=("Exported files are unreviewed: a fabricator's own checks and a qualified engineer must review them before anything is ordered.",),
    )


__all__ = ["TOOL_NAMES", "drc", "erc", "export", "info", "server"]
