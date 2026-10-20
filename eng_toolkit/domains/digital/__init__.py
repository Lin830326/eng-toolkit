"""``digital`` domain: Verilog synthesis, lint and equivalence checking with Yosys (a program the user installs)."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.digital.tools import TOOL_NAMES, server

DOMAIN = DomainInfo(
    name="digital",
    title="Digital logic (Verilog with Yosys)",
    description=(
        "Synthesis of Verilog to a generic gate netlist (cell counts, longest path in gate levels, a unit-gate area estimate), lint "
        "(syntax, hierarchy, undriven and multiply driven signals, latches, combinational loops) and equivalence checking of two "
        "designs (combinational proof or bounded sequential check, with a counterexample). Needs Yosys, which the user installs "
        "separately; the Verilog is treated as data and file-reading constructs are refused."
    ),
    engines=(),
    server=server,
    tools=TOOL_NAMES,
)

__all__ = ["DOMAIN", "server"]
