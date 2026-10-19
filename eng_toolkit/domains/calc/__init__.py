"""``calc`` domain: closed-form and correlation-based engineering calculations."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.calc import components  # noqa: F401  (registers its tools on the same server)
from eng_toolkit.domains.calc.hydraulics import TOOL_NAMES as _HYDRAULICS_TOOLS
from eng_toolkit.domains.calc.hydraulics import server

DOMAIN = DomainInfo(
    name="calc",
    title="Engineering calculations (hydraulics)",
    description=(
        "Unit-checked hydraulic calculations: cylinder force and speed, pipe pressure drop "
        "(Darcy-Weisbach with Colebrook friction factor via the MIT-licensed `fluids`), orifice flow, "
        "pump power and accumulator pre-charge sizing."
    ),
    engines=("pint", "fluids"),
    server=server,
    tools=_HYDRAULICS_TOOLS + components.TOOL_NAMES,
)

__all__ = ["DOMAIN", "server"]
