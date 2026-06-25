from __future__ import annotations
"""BAW — MCP Bridge: connect to MCP servers, discover and invoke tools.

Supports stdio and SSE/HTTP transport types.
MCP tools are NOT registered as BAW TOOL_DEFs at startup — they're
discovered dynamically via the `mcp_call` tool.

Config in config.yaml:
  mcp:
    servers:
      memory:
        command: npx
        args: ["-y", "@modelcontextprotocol/server-memory"]
        disabled: false
"""

import json
import os
import asyncio
import shlex
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("baw.mcp")


# ── Global registry of connected MCP servers ──

_connections: dict[str, Any] = {}  # server_name -> {"session": ..., "tools": {...}}
_lock = None  # lazy init — needs event loop
def _get_lock():
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


async def _connect_stdio(name: str, command: str, args: list[str],
                         env: dict | None = None) -> dict | None:
    """Connect to a stdio-based MCP server."""
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        server_params = StdioServerParameters(
            command=command,
            args=args or [],
            env=env or None,
        )

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools_result = await session.list_tools()

                tools = {}
                for tool in tools_result.tools:
                    tools[tool.name] = {
                        "name": tool.name,
                        "description": tool.description or "",
                        "input_schema": tool.inputSchema if hasattr(tool, 'inputSchema') else {},
                        "session": session,
                    }

                return {
                    "name": name,
                    "transport": "stdio",
                    "session": session,
                    "tools": tools,
                    "read": read,
                    "write": write,
                }
    except ImportError:
        logger.error("MCP package not installed. Run: pip install mcp")
        return None
    except Exception as e:
        logger.error(f"MCP connect error ({name}): {e}")
        return None


def _load_mcp_config(config_path: str = "") -> dict:
    """Load MCP server configuration."""
    search_paths = [
        Path(config_path) if config_path else None,
        Path.home() / ".baw" / "mcp.json",
        Path.cwd() / "mcp.json",
    ]

    for p in search_paths:
        if p and p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                return raw.get("mcpServers", raw)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Failed to load MCP config from {p}: {e}")

    return {}


def _dispatcher(action: str, server: str = "", tool: str = "",
                args: str = "{}", config_path: str = "") -> str:
    """Dispatch MCP actions.

    'connect' — connect to all configured MCP servers (or a specific one)
    'list_servers' — show connected servers
    'list_tools' — show tools from a connected server
    'call' — call an MCP tool (server + tool + args)
    'disconnect_all' — close all MCP connections
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        if action == "connect":
            result = loop.run_until_complete(_connect_all(config_path))
        elif action == "list_servers":
            result = _list_servers()
        elif action == "list_tools":
            result = _list_tools(server)
        elif action == "call":
            result = loop.run_until_complete(_call_tool(server, tool, args))
        elif action == "disconnect_all":
            result = loop.run_until_complete(_disconnect_all())
        else:
            result = (f"Error: unknown action '{action}'. "
                      f"Available: connect, list_servers, list_tools, call, disconnect_all")
        return result
    finally:
        loop.close()


async def _connect_all(config_path: str = "") -> str:
    """Connect to all configured MCP servers."""
    config = _load_mcp_config(config_path)
    if not config:
        return ("No MCP servers configured. Create ~/.baw/mcp.json "
                "with {\"mcpServers\": {\"memory\": {\"command\": \"npx\", \"args\": []}}}")

    results = []
    async with _get_lock():
        for name, cfg in config.items():
            if cfg.get("disabled", False):
                results.append(f"⏭️  {name}: disabled")
                continue

            command = cfg.get("command", "")
            args = cfg.get("args", [])
            env = cfg.get("env", None)
            conn_type = cfg.get("type", "stdio")

            if conn_type == "stdio" and command:
                conn = await _connect_stdio(name, command, args, env)
                if conn:
                    _connections[name] = conn
                    tool_count = len(conn["tools"])
                    results.append(f"✅ {name}: connected ({tool_count} tools)")
                else:
                    results.append(f"❌ {name}: connection failed")
            else:
                results.append(f"⏭️  {name}: unsupported transport '{conn_type}'")

    return "\n".join(results) if results else "No MCP servers to connect."


def _list_servers() -> str:
    """List connected MCP servers and their tools."""
    if not _connections:
        return "No MCP servers connected."

    lines = ["## Connected MCP Servers:"]
    for name, conn in _connections.items():
        tool_count = len(conn.get("tools", {}))
        transport = conn.get("transport", "?")
        lines.append(f"  - {name} ({transport}, {tool_count} tools)")

    return "\n".join(lines)


def _list_tools(server: str) -> str:
    """List tools from a specific MCP server."""
    if not server:
        if not _connections:
            return "No MCP servers connected."
        # List all tools from all servers
        lines = ["## Available MCP Tools:"]
        for sname, conn in _connections.items():
            for tname, tinfo in conn.get("tools", {}).items():
                desc = tinfo.get("description", "")[:80]
                lines.append(f"  [{sname}] {tname} — {desc}")
        return "\n".join(lines)

    conn = _connections.get(server)
    if not conn:
        return f"Server '{server}' not connected. Try 'connect' first."

    tools = conn.get("tools", {})
    if not tools:
        return f"Server '{server}' has no tools."

    lines = [f"## Tools on '{server}':"]
    for tname, tinfo in tools.items():
        desc = tinfo.get("description", "")[:100]
        schema = tinfo.get("input_schema", {})
        props = list(schema.get("properties", {}).keys()) if schema else []
        props_str = ", ".join(props[:5]) if props else ""
        lines.append(f"  - `{tname}` — {desc}")
        if props_str:
            lines.append(f"    params: {props_str}")
    return "\n".join(lines)


async def _call_tool(server: str, tool: str, args: str) -> str:
    """Call a tool on a connected MCP server."""
    if not server or not tool:
        return "Error: both 'server' and 'tool' are required for 'call'."

    conn = _connections.get(server)
    if not conn:
        return f"Error: server '{server}' not connected."

    tinfo = conn["tools"].get(tool)
    if not tinfo:
        available = ", ".join(conn["tools"].keys())
        return f"Error: tool '{tool}' not found on '{server}'. Available: {available}"

    # Parse arguments
    try:
        arguments = json.loads(args) if args and args.strip() else {}
    except json.JSONDecodeError as e:
        return f"Error: invalid JSON in args: {e}"

    # Call the tool
    try:
        session = conn["session"]
        result = await session.call_tool(tool, arguments)
        # Extract text content
        if hasattr(result, "content"):
            texts = []
            for item in result.content:
                if hasattr(item, "text"):
                    texts.append(item.text)
            output = "\n".join(texts) if texts else str(result.content)
        else:
            output = str(result)
        return output[:5000]  # cap output
    except Exception as e:
        return f"Error calling MCP tool '{tool}': {e}"


async def _disconnect_all() -> str:
    """Disconnect all MCP servers."""
    async with _get_lock():
        count = len(_connections)
        for name, conn in list(_connections.items()):
            try:
                session = conn.get("session")
                if session:
                    await session.close()
            except Exception:
                pass
        _connections.clear()
    return f"✅ Disconnected {count} MCP server(s)."


# ── TOOL_DEF ──

TOOL_DEF = {
    "name": "mcp",
    "description": (
        "MCP (Model Context Protocol) bridge — connect to MCP servers "
        "and call their tools. Use 'connect' to start servers (reads ~/.baw/mcp.json). "
        "Use 'list_servers' to see connected servers. "
        "Use 'list_tools' to discover available tools (optionally filter by server). "
        "Use 'call' to invoke a tool: mcp(server='<name>', tool='<toolname>', args='{\"key\": \"val\"}')."
    ),
    "handler": _dispatcher,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["connect", "list_servers", "list_tools", "call", "disconnect_all"],
                "description": "What to do.",
            },
            "server": {
                "type": "string",
                "description": "MCP server name (for 'list_tools' and 'call').",
            },
            "tool": {
                "type": "string",
                "description": "Tool name (for 'call').",
            },
            "args": {
                "type": "string",
                "description": "JSON string of tool arguments (for 'call').",
                "default": "{}",
            },
            "config_path": {
                "type": "string",
                "description": "Path to mcp.json config file.",
                "default": "",
            },
        },
        "required": ["action"],
    },
    "risk_level": "medium",
}
