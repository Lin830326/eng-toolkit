"""Engines of the ``drawing`` domain that are not Python distributions: programs the user installs and the toolkit starts as separate processes.

Loaded by path by ``eng_toolkit.licenses.register_domain_engines``; imports nothing of the domain itself.
"""

from eng_toolkit.external_catalog import DWG2DXF

EXTERNAL_TOOLS = (DWG2DXF,)
