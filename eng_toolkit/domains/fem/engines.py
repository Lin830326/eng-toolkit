"""The programs the ``fem`` domain starts as separate processes: Gmsh (meshing) and CalculiX (solver).  Never imported, linked or bundled."""

from eng_toolkit.external_catalog import CCX, GMSH

EXTERNAL_TOOLS = (GMSH, CCX)
