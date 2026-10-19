"""``mech`` domain: closed-form mechanical-design checks (bolts, rolling bearings, springs, beams)."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.mech import beams, bearings, fasteners, springs  # noqa: F401  (register their tools)
from eng_toolkit.domains.mech._server import server

TOOL_NAMES: tuple[str, ...] = ("bolt_check", "bearing_life", "spring_compression", "beam_bending")

DOMAIN = DomainInfo(
    name="mech",
    title="Mechanical design checks",
    description=(
        "Unit-checked textbook calculations: bolt tension check with torque-preload relation, rolling-bearing basic "
        "rating life L10 (ISO 281 formula), helical compression spring rate and stress (Wahl), and closed-form beam "
        "cases (reactions, moment, deflection, bending stress)."
    ),
    engines=("pint",),
    server=server,
    tools=TOOL_NAMES,
)

__all__ = ["DOMAIN", "TOOL_NAMES", "server"]
