"""The OCP import guard: the one place that turns a missing or unloadable wheel into ``EngineUnavailable``."""

from __future__ import annotations

from eng_toolkit.errors import EngineUnavailable
from eng_toolkit.licenses import install_hint

MODULE = "OCP"
DIST = "cadquery-ocp-novtk"


def load() -> None:
    """Check that OCP imports and keep OCCT's own printer off stdout.

    Importing the package (not a submodule) is what fails when the wheel is absent or its DLLs do not load.
    """
    try:
        import OCP  # noqa: F401
        from OCP.Message import Message, Message_PrinterOStream
    except (ImportError, OSError) as exc:
        raise EngineUnavailable(MODULE, DIST, exc, install=install_hint(DIST)) from exc
    # OCCT prints STEP parse errors to stdout, which is the MCP stdio channel.
    Message.DefaultMessenger_s().RemovePrinters(Message_PrinterOStream.get_type_descriptor_s())
