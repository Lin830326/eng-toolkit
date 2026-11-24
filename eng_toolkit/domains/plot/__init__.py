"""``plot`` domain: deterministic, self-contained SVG charts of tables (x-y, stacked panels, bars and histograms)."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.plot import bar, panels, xy  # noqa: F401  (register their tools)
from eng_toolkit.domains.plot._server import server

TOOL_NAMES: tuple[str, ...] = ("xy", "panels", "bar")

DOMAIN = DomainInfo(
    name="plot",
    title="Charts (SVG, PNG)",
    description=(
        "Turns tables (the CSV artifacts of the other tools, or inline JSON series) into clean, deterministic, self-contained SVG "
        "charts: line/scatter/step/stem plots with log axes, a right axis, reference lines and bands; stacked panels sharing x; "
        "bar charts and histograms. Escaped text, no scripts, colour-blind-safe palette, light/dark/print themes. the default format=both also writes a 1600 px PNG (optional png extra), returned as an image in the reply. numpy only otherwise."
    ),
    engines=("numpy",),
    server=server,
    tools=TOOL_NAMES,
)

__all__ = ["DOMAIN", "TOOL_NAMES", "server"]
