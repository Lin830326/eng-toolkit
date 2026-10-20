"""Engines of the ``pcb`` domain: no Python distribution; KiCad's command line is started as a separate program."""

from eng_toolkit.external_catalog import KICAD_CLI

ENGINES = ()
EXTERNAL_TOOLS = (KICAD_CLI,)
