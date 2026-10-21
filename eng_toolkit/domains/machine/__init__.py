"""Textbook machine-element calculations, with no additional engines."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.machine import belt, gear, key, pressure_vessel, shaft, weld  # noqa: F401
from eng_toolkit.domains.machine._server import server

TOOL_NAMES = ("shaft", "gear", "weld", "pressure_vessel", "belt", "key")
DOMAIN = DomainInfo(
    name="machine", title="Machine elements",
    description="Shaft static/fatigue, spur gears, weld groups, elastic pressure vessels, belts, keys and splines.",
    engines=("pint",), server=server, tools=TOOL_NAMES,
)
