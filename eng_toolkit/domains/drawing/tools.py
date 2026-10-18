"""``drawing`` sub-server: orthographic views of a 3D model on an ISO sheet, as DXF and PDF.

The tool validates the caller's strings (``sheet.py``), asks :mod:`eng_toolkit.brep` for the hidden-line projections,
lets ``sheet.py`` lay them out and ``dxf.py`` write the files.  Nothing here knows OpenCASCADE or ezdxf.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from eng_toolkit import __version__, brep
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.drawing import dxf
from eng_toolkit.domains.drawing import sheet as S
from eng_toolkit.errors import ToolFailure
from eng_toolkit.licenses import install_hint
from eng_toolkit.runner import Computed, JobHandle, run_tool

ENGINE_MAKE_VIEWS = "eng_toolkit.drawing.make_views"
TOOL_NAMES: tuple[str, ...] = ("make_views",)
FORMATS: tuple[str, ...] = ("dxf", "pdf")
LATER_FORMATS: tuple[str, ...] = ("svg", "png")
COVERAGE = "dimensions: overall size only; hole callouts, centre lines, ordinates, SVG/PNG: later slices"
DETERMINISM = (
    "The same model and arguments give the same bytes in drawing.dxf and drawing.pdf, in any process: ezdxf's fixed "
    "metadata option is set while writing, and the CLASS records of the DXF, whose order would depend on PYTHONHASHSEED, "
    "are sorted. The PDF has no dates or producer, no compression and a fixed object order."
)

server = FastMCP(
    "drawing",
    instructions=(
        "Engineering drawings from 3D CAD files. Lengths are millimetres on the sheet. "
        "Results are unreviewed and must be checked by a qualified engineer."
    ),
)


def _parse_formats(text: str) -> list[str]:
    """The files to write, dxf first; dxf is the source of the drawing, so it is always among them."""
    wanted = [item.strip().lower() for item in text.split(",") if item.strip()]
    later = [item for item in wanted if item in LATER_FORMATS]
    if later:
        raise ToolFailure(
            "input.invalid",
            f"formats: {later} arrive in a later version; use a comma list of {list(FORMATS)}",
            {"param": "formats", "allowed": list(FORMATS), "later": later},
        )
    unknown = [item for item in wanted if item not in FORMATS]
    if unknown:
        raise ToolFailure(
            "input.invalid",
            f"formats: {unknown} is not a format; use a comma list of {list(FORMATS)}",
            {"param": "formats", "allowed": list(FORMATS)},
        )
    return [fmt for fmt in FORMATS if fmt == "dxf" or fmt in wanted]


MAKE_VIEWS_DESCRIPTION = (
    "Make an engineering drawing of a 3D model (.step .stp .iges .igs .brep): orthographic views with hidden lines "
    "removed exactly, on an ISO 5457 sheet with frame and title block, one overall dimension per model axis (true size at "
    "any scale) and your notes. Writes drawing.dxf (R2018, millimetres, the source) and drawing.pdf (vector, true scale; "
    "its text is outlines, so it has no text layer). "
    f"views: comma list of {', '.join(S.VIEW_NAMES)} (at most 7, no repeats). projection: third_angle (default) or first_angle. "
    f"sheet: auto or {', '.join(S.SHEETS)} (landscape). scale: auto or {', '.join(S.scale_text(s) for s in S.SCALES)}; auto "
    "takes the largest standard scale and then the smallest sheet at which the views, their dimension rows and the title "
    "block fit, and enlarges small parts. dimensions: auto or overall (X, Y and Z once each) or none. The title block "
    "always says UNREVIEWED. Not a complete manufacturing drawing: no tolerances, fits, hole callouts or centre lines yet. "
    f"Needs the optional drawing engine ({install_hint('ezdxf')}). svg and png are not available yet."
)


@server.tool(name="make_views", description=MAKE_VIEWS_DESCRIPTION)
def make_views(
    model_path: Annotated[str, Field(description="Path to the model: .step .stp .iges .igs or .brep")],
    views: Annotated[str, Field(description=f"Comma list of views, from {', '.join(S.VIEW_NAMES)}; at most 7, no repeats")] = "front,top,right",
    projection: Annotated[str, Field(description="third_angle (default) or first_angle")] = "third_angle",
    sheet: Annotated[str, Field(description=f"auto or one of {', '.join(S.SHEETS)} (ISO 5457, landscape)")] = "auto",
    scale: Annotated[str, Field(description=f"auto or an ISO 5455 scale: {', '.join(S.scale_text(s) for s in S.SCALES)}")] = "auto",
    hidden_lines: Annotated[bool, Field(description="Draw the hidden edges as dashed lines")] = True,
    dimensions: Annotated[str, Field(description="auto (= overall), overall (each axis once) or none")] = "auto",
    title: Annotated[str, Field(description="Title block title, at most 80 characters; any language")] = "",
    part_number: Annotated[str, Field(description="Title block part number, at most 40 characters")] = "",
    material: Annotated[str, Field(description="Title block material, at most 40 characters")] = "",
    revision: Annotated[str, Field(description="Title block revision, at most 8 characters")] = "A",
    general_tolerance: Annotated[str, Field(description="Printed as text only, e.g. 'ISO 2768-m'; at most 80 characters")] = "",
    notes: Annotated[str, Field(description="Notes separated by ';', at most 6 of at most 100 characters; printed above the title block")] = "",
    formats: Annotated[str, Field(description="Comma list of dxf, pdf. The DXF is always written. svg and png come later")] = "dxf,pdf",
) -> ToolResult:
    inputs = {
        "model_path": model_path,
        "views": views,
        "projection": projection,
        "sheet": sheet,
        "scale": scale,
        "hidden_lines": hidden_lines,
        "dimensions": dimensions,
        "title": title,
        "part_number": part_number,
        "material": material,
        "revision": revision,
        "general_tolerance": general_tolerance,
        "notes": notes,
        "formats": formats,
    }

    def compute(job: JobHandle) -> Computed:
        names = S.parse_views(views)
        method = S.parse_method(projection)
        sheet_arg, scale_arg, mode = S.parse_sheet(sheet), S.parse_scale(scale), S.parse_dimensions(dimensions)
        block = S.TitleBlock(
            title=S.clean_text("title", title, S.LIMITS["title"]),
            part_number=S.clean_text("part_number", part_number, S.LIMITS["part_number"]),
            material=S.clean_text("material", material, S.LIMITS["material"]),
            revision=S.clean_text("revision", revision, S.LIMITS["revision"]),
            general_tolerance=S.clean_text("general_tolerance", general_tolerance, S.LIMITS["general_tolerance"]),
            notes=S.parse_notes(notes),
        )
        wanted = _parse_formats(formats)
        dxf.require_engine()

        model_file = job.input_file(model_path, param="model_path", extensions=brep.SOLID_EXTENSIONS)
        solid = brep.read_solid(model_file, param="model_path")
        if solid.faces == 0:
            raise ToolFailure("input.invalid", f"model_path: no faces in {str(model_file)!r}", {"path": str(model_file)})
        projections = [brep.project(solid, name, hidden=hidden_lines) for name in names]
        drawing = S.compose(projections, method=method, sheet=sheet_arg, scale=scale_arg, dimensions=mode, title=block)

        artifacts = [job.write_bytes("drawing.dxf", dxf.write_dxf(drawing), fmt="dxf")]
        if "pdf" in wanted:
            artifacts.append(job.write_bytes("drawing.pdf", dxf.render_pdf(drawing), fmt="pdf"))

        warnings: list[str] = []
        for view in drawing.views:
            if view.projection.method == "polygonal":
                warnings.append(
                    f"{view.name} view: the model has too many faces for the exact algorithm, so the view is polygonal: "
                    "straight segments only, arcs and circles come out as polylines within about 0.01 mm"
                )
            elif view.projection.method == "exact_unverified":
                warnings.append(
                    f"{view.name} view: the exact projection could not be cross-checked against a polygonal one, so "
                    "hidden-line errors near fillets and hole rims are possible; check this view against the model"
                )
        warnings += dxf.text_warnings(drawing)
        warnings += [f"Layout: {w}" for w in drawing.warnings]
        if "pdf" in wanted:
            warnings.append("The PDF has no text layer: text is drawn as glyph outlines, so it cannot be searched or copied. The DXF holds the text.")

        values = {
            "scale": QuantityOut(quantity=drawing.scale_value, unit="dimensionless"),
            "view_count": QuantityOut(quantity=float(len(drawing.views)), unit="dimensionless"),
            "dimension_count": QuantityOut(quantity=float(len(drawing.dims)), unit="dimensionless"),
            "sheet_width": QuantityOut(quantity=drawing.size_mm[0], unit="millimeter"),
            "sheet_height": QuantityOut(quantity=drawing.size_mm[1], unit="millimeter"),
        }
        data: dict[str, Any] = {
            "sheet": {
                "name": drawing.sheet,
                "size_mm": list(drawing.size_mm),
                "scale_text": drawing.scale_label,
                "projection": drawing.method,
            },
            "views": [
                {
                    "name": v.name,
                    "origin_mm": list(v.origin),
                    "extent_mm": list(v.extent),
                    "visible_primitives": len(v.projection.visible),
                    "hidden_primitives": len(v.projection.hidden),
                    "method": v.projection.method,
                    "verified": v.projection.verified,
                    "dropped": dict(v.projection.dropped),
                }
                for v in drawing.views
            ],
            "dimensions": [
                {"kind": d.kind, "view": d.view, "orientation": d.orientation, "value_mm": d.value_mm, "text": d.text, "source": d.source}
                for d in drawing.dims
            ],
            "layout_warnings": list(drawing.warnings),
            "coverage": COVERAGE,
            "determinism": DETERMINISM,
            "formats": wanted,
        }
        return Computed(values=values, primary="scale", warnings=warnings, data=data, artifacts=artifacts)

    return run_tool(
        tool_name="drawing_make_views",
        engine=ENGINE_MAKE_VIEWS,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("cadquery-ocp-novtk", "ezdxf", "numpy"),
        standing_warnings=(
            "Dimensions are derived from the bounding box of the projected geometry: overall size only. The drawing is "
            "unreviewed and not a complete manufacturing drawing (no tolerances, fits, surface finish, hole callouts or "
            "centre lines); check it against the model before anyone builds from it.",
        ),
    )


__all__ = ["TOOL_NAMES", "make_views", "server"]
