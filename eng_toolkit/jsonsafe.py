"""JSON parsing of caller text that fails as a JSON error instead of a RecursionError or a bare ValueError.

``json.loads`` raises ``RecursionError`` for a document nested a thousand levels deep, and a plain ``ValueError`` for an
integer written with more than 4300 digits. Callers already turn ``json.JSONDecodeError`` into a refusal that names the
parameter, so this module reports both the same way.
"""

from __future__ import annotations

import json
from typing import Any


def loads(text: str | bytes | bytearray, **kwargs: Any) -> Any:
    doc = text if isinstance(text, str) else ""
    try:
        return json.loads(text, **kwargs)
    except json.JSONDecodeError:
        raise
    except RecursionError:
        raise json.JSONDecodeError("the JSON is nested too deeply", doc, 0) from None
    except ValueError as exc:
        # int() refuses more than 4300 digits; refusals raised by the callers' own hooks are ToolFailure, a ValueError too
        from eng_toolkit.errors import ToolFailure

        if isinstance(exc, ToolFailure):
            raise
        raise json.JSONDecodeError(f"the JSON holds a number that cannot be read ({str(exc)[:80]})", doc, 0) from None
