"""``calc`` domain: closed-form and correlation-based engineering calculations."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.calc.hydraulics import TOOL_NAMES, server

DOMAIN = DomainInfo(
    name="calc",
    title="Engineering calculations (hydraulics)",
    description=(
        "Unit-checked hydraulic calculations: cylinder force and pipe pressure drop "
        "(Darcy-Weisbach with Colebrook friction factor via the MIT-licensed `fluids`)."
    ),
    engines=("pint", "fluids"),
    server=server,
    tools=TOOL_NAMES,
)

__all__ = ["DOMAIN", "server"]
