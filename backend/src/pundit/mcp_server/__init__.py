"""Read-only MCP server over the Stats Tools (docs/day6-design.md section 3).

`tools` holds the tools as plain functions (no SDK); `server` registers them on the MCP SDK for
stdio (`pundit mcp`) and for `/mcp` in the API (api/mcp_mount.py adds the live sessions).
"""
