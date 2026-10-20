"""Programs the ``spice`` domain starts as separate processes (ngspice is BSD-3-Clause; the descriptor lives in the shared catalog)."""

from eng_toolkit.external_catalog import NGSPICE

EXTERNAL_TOOLS = (NGSPICE,)
