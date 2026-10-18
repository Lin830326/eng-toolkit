"""The one owner of OpenCASCADE (OCP, optional extra ``cad``) and of 3D model files.

Lengths are millimetres.  OCP is imported inside functions, so importing this package never needs
the wheel; a missing or unloadable one raises :class:`EngineUnavailable` from the first call that
needs it.  Nothing outside this package may import OCP or reach for ``Solid._handle``.
"""

from eng_toolkit.brep._files import read_solid, triangulate
from eng_toolkit.brep._occ import load as require_engine
from eng_toolkit.brep._types import Mesh, Solid
from eng_toolkit.errors import EngineUnavailable

__all__ = ["EngineUnavailable", "Mesh", "Solid", "read_solid", "require_engine", "triangulate"]
