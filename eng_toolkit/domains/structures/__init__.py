"""Closed forms and linear structural analysis, using only NumPy and Pint."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.structures import column, frame, modal, section, stress, truss  # noqa: F401
from eng_toolkit.domains.structures._server import server

TOOL_NAMES = ("section", "truss", "frame", "modal", "column", "stress")
DOMAIN = DomainInfo(
    name="structures", title="Structural analysis",
    description="Section properties, trusses, plane frames, consistent-mass modes, column buckling and stress states.",
    engines=("numpy", "pint"), server=server, tools=TOOL_NAMES,
)
