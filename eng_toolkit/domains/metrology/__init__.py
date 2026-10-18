"""``metrology`` domain: ISO 286 fits and tolerance stack-up."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.metrology.tools import TOOL_NAMES, server

DOMAIN = DomainInfo(
    name="metrology",
    title="Dimensional metrology (tolerances)",
    description=(
        "ISO 286 limits and fits (IT01-IT18, 3-500 mm) from an in-repo table, reverse search "
        "of standard fits for a clearance window, and 1-D tolerance stack-up by worst case, "
        "RSS and optional seeded Monte Carlo (NumPy)."
    ),
    engines=("pint", "numpy"),
    server=server,
    tools=TOOL_NAMES,
)

__all__ = ["DOMAIN", "server"]
