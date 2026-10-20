"""``spice`` domain: circuit simulation with ngspice, a netlist allow-list, and measurements on the result tables."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.spice import measure, tools  # noqa: F401  (register their tools on the server)
from eng_toolkit.domains.spice._server import server
from eng_toolkit.domains.spice.tools import TOOL_NAMES

DOMAIN = DomainInfo(
    name="spice",
    title="Circuit simulation (ngspice)",
    description=(
        "Operating point, DC sweep, AC and transient simulation of a netlist with ngspice (a program the user installs; started as a separate "
        "process in batch mode, never bundled). The netlist is read as data through an allow-list: control sections, includes, libraries, "
        "code models and file output are refused. Includes a static netlist check and measurements (rise time, overshoot, -3 dB bandwidth, ...) "
        "on the result tables."
    ),
    engines=("pint",),
    server=server,
    tools=TOOL_NAMES,
)

__all__ = ["DOMAIN", "server"]
