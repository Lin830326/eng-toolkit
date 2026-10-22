"""Uniaxial material fatigue, cycle counting and linear elastic fracture mechanics."""

from eng_toolkit.domains import DomainInfo
from . import fracture, material, rainflow, sn, strain_life  # noqa: F401
from ._server import server

TOOL_NAMES = ("sn", "rainflow", "strain_life", "fracture", "material")
DOMAIN = DomainInfo(
    name="fatigue", title="Material fatigue and fracture",
    description="Stress-life, rainflow, local strain-life, LEFM crack growth and material-data reductions.",
    engines=("numpy", "pint"), server=server, tools=TOOL_NAMES,
)
