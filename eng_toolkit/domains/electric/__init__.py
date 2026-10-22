"""Steady-state electrical engineering calculators using NumPy and Pint."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.electric import converter, drive_sizing, induction_motor, pm_dc_motor, power_flow, transformer  # noqa: F401
from eng_toolkit.domains.electric._server import server

TOOL_NAMES = ("transformer", "induction_motor", "pm_dc_motor", "power_flow", "drive_sizing", "converter")
DOMAIN = DomainInfo(
    name="electric", title="Electrical machines, power and drives",
    description="Transformer, induction and PM/DC machines, three-phase power, drive sizing and nonisolated DC-DC steady state.",
    engines=("numpy", "pint"), server=server, tools=TOOL_NAMES,
)
