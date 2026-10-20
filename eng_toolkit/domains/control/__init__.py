"""``control`` domain: classical SISO control analysis (poles and stability, time response, frequency response, feedback, PID tuning, discretisation)."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.control import analyze, discretize, feedback, frequency, pid, response  # noqa: F401  (register their tools)
from eng_toolkit.domains.control._server import server

TOOL_NAMES: tuple[str, ...] = ("analyze", "response", "frequency", "feedback", "pid_tune", "discretize")

DOMAIN = DomainInfo(
    name="control",
    title="Classical control analysis",
    description=(
        "SISO linear systems from coefficient lists: poles, zeros and exact Routh/Jury stability; step, impulse and ramp "
        "response with rise/overshoot/settling metrics; Bode data, margins and closed-loop stability; feedback loops, error "
        "constants and sensitivity; PID tuning rules with a simulated check; continuous-to-discrete conversion. Pure Python "
        "with numpy, no scipy."
    ),
    engines=("numpy",),
    server=server,
    tools=TOOL_NAMES,
)

__all__ = ["DOMAIN", "TOOL_NAMES", "server"]
