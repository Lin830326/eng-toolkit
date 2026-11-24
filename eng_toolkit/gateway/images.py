"""Hand a tool's PNG picture to the chat as an MCP image block.

A tool that writes a PNG (``plot_*``, ``geometry_view``, ``fem_view``) returns only its path in the structured result, and a
chat client cannot open a path. Tools that declare the boolean parameter ``inline_image`` (default true) get the PNG
attached to the reply as an ``ImageContent`` block (base64, ``image/png``) next to the usual text block.

* The structured result (the unified ``ToolResult``) stays as it is, ``data.png_path`` included.
* A PNG above ``INLINE_IMAGE_MAX_BYTES`` is not attached; the structured ``warnings`` say so and name the path.
* Only the result's own PNG artifact is read: it must lie in the job directory and match the artifact's SHA-256.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from fastmcp.server.middleware import Middleware
from fastmcp.tools import ToolResult as MCPToolResult
from mcp.types import ImageContent, TextContent
from pydantic import TypeAdapter, ValidationError

from eng_toolkit.inline_image import INLINE_IMAGE_MAX_BYTES, PARAM, skipped_warning

_BOOL = TypeAdapter(bool)


def _wanted(value: Any) -> bool:
    try:
        return _BOOL.validate_python(value)
    except ValidationError:
        return False  # the tool refuses it (or already did); nothing to attach


def _png_artifact(structured: dict[str, Any]) -> tuple[Path, str] | None:
    """The result's PNG file and its recorded SHA-256, when it is inside the job directory."""
    data = structured.get("data")
    job_dir = data.get("job_dir") if isinstance(data, dict) else None
    if not isinstance(job_dir, str):
        return None
    for artifact in structured.get("artifacts") or []:
        if isinstance(artifact, dict) and artifact.get("format") == "png" and isinstance(artifact.get("path"), str):
            path = Path(artifact["path"]).resolve()
            if path.is_relative_to(Path(job_dir).resolve()) and path.is_file():
                return path, str(artifact.get("sha256", ""))
    return None


def attach_image(structured: dict[str, Any]) -> tuple[ImageContent | None, str | None]:
    """The image block for a successful structured result, or the warning that explains why there is none."""
    if not structured.get("ok"):
        return None, None
    found = _png_artifact(structured)
    if found is None:
        return None, None
    path, sha256 = found
    size = path.stat().st_size
    if size > INLINE_IMAGE_MAX_BYTES:
        return None, skipped_warning(size)
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != sha256:
        return None, None
    return ImageContent(type="image", data=base64.b64encode(payload).decode("ascii"), mimeType="image/png"), None


class InlineImages(Middleware):
    """Attach the PNG artifact to the reply of every tool that declares ``inline_image``."""

    async def on_call_tool(self, context, call_next):
        result = await call_next(context)
        structured = getattr(result, "structured_content", None)
        if not isinstance(structured, dict):
            return result
        arguments = context.message.arguments or {}
        if not _wanted(arguments.get(PARAM, True)):
            return result
        tool = await context.fastmcp_context.fastmcp.get_tool(context.message.name)
        if tool is None or PARAM not in (tool.parameters or {}).get("properties", {}):
            return result
        try:
            image, warning = attach_image(structured)
        except OSError:
            return result  # a missing or unreadable file never turns a good result into an error
        if image is None and warning is None:
            return result
        content = list(result.content)
        if image is not None:
            return MCPToolResult(content=[*content, image], structured_content=structured, meta=result.meta)
        structured = {**structured, "warnings": [*structured.get("warnings", []), warning]}
        text = json.dumps(structured, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        for i, block in enumerate(content):
            if isinstance(block, TextContent):
                content[i] = TextContent(type="text", text=text)
                break
        return MCPToolResult(content=content, structured_content=structured, meta=result.meta)


__all__ = ["InlineImages", "attach_image"]
