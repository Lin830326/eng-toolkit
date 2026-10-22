"""Steady hydraulics, gas relations and single-pipe transients."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.fluids import compressible, external_flow, open_channel, pipe_network, pump_system, surge  # noqa: F401
from eng_toolkit.domains.fluids._server import server

TOOL_NAMES = ("pipe_network", "pump_system", "open_channel", "compressible", "surge", "external_flow")
DOMAIN = DomainInfo(
    name="fluids", title="Fluid systems",
    description="Pipe networks, pump systems, open channels, ideal-gas relations, water hammer and external drag.",
    engines=("numpy", "pint"), server=server, tools=TOOL_NAMES,
)
