"""MCP gateway: one server that mounts every domain sub-server under a namespace."""

from eng_toolkit.gateway.server import gateway, main

__all__ = ["gateway", "main"]
