---
description: Check that the eng-toolkit MCP server is connected and show how to register it
---

Check whether the `eng-toolkit` MCP tools (names starting with `mcp__eng-toolkit__`) are available in this session.

- If they are, call `toolkit_doctor` and summarise: which domains are installed, which external programs are missing (KiCad, ngspice, Yosys, Gmsh, CalculiX, LibreDWG are optional) and which optional Python extras (`cad`, `drawing`, `png`, `scan`) are missing, with the install command the tool reports.
- If they are not, tell the user to register the server once (adjust the path to where the repository is cloned) and restart Claude Code:

```bash
claude mcp add --scope user eng-toolkit -- uv run --extra cad --extra drawing --extra png --directory "C:\path\to\eng-toolkit" eng-toolkit
```

Then run `/mcp` to confirm `eng-toolkit` is connected, and `/eng-toolkit-setup` again.
