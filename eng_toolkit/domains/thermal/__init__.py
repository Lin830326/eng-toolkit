"""``thermal`` domain: closed-form heat-transfer calculations (conduction, exchangers, fins, transients, radiation, convection)."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.thermal import (  # noqa: F401  (register their tools)
    conduction,
    convection,
    exchanger,
    fins,
    radiation,
    transient,
)
from eng_toolkit.domains.thermal._server import server

TOOL_NAMES: tuple[str, ...] = ("conduction", "exchanger", "fin", "transient", "radiation", "convection")

DOMAIN = DomainInfo(
    name="thermal",
    title="Heat transfer calculations",
    description=(
        "Unit-checked textbook heat transfer: steady conduction through layered walls, pipes and spheres with films; heat "
        "exchangers by effectiveness-NTU and LMTD; fins; transient conduction (lumped, one-term series, semi-infinite); "
        "grey-body radiation exchange; convection Nusselt correlations. Pure Python, fluid properties are inputs."
    ),
    engines=("pint",),
    server=server,
    tools=TOOL_NAMES,
)

__all__ = ["DOMAIN", "TOOL_NAMES", "server"]
