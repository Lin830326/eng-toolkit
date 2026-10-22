"""Bounded numerical optimization, inference and experimental design."""

from eng_toolkit.domains import DomainInfo
from . import doe, fit, linprog, minimize, root, sensitivity  # noqa: F401
from ._server import server

TOOL_NAMES = ("minimize", "linprog", "root", "fit", "sensitivity", "doe")
DOMAIN = DomainInfo(name="optimize", title="Numerical optimization and experiments",
                    description="Bounded arithmetic expressions, constrained optimization, LP/MIP, roots, fitting, uncertainty and experiments.",
                    engines=("numpy", "pint"), server=server, tools=TOOL_NAMES)
