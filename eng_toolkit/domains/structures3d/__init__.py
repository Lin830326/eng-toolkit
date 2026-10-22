"""Independent spatial-frame, plate and elastic-foundation domain."""

from eng_toolkit.domains import DomainInfo
from . import buckling, foundation, modal, plate, plate_stability, static  # noqa: F401
from ._server import server

TOOL_NAMES = ("static", "modal", "buckling", "plate", "plate_stability", "foundation")
DOMAIN = DomainInfo(name="structures3d", title="Space frames and plates",
                    description="3D frames, modal analysis, linear buckling, thin plates and Winkler foundations.",
                    engines=("numpy", "pint"), server=server, tools=TOOL_NAMES)
