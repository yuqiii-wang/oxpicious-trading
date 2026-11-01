"""llm_agents.deep_agents.core.mcp — the MCP side of the agent.

``McpToolClient`` owns one MCP client session and nothing else:

  * transport — streamable HTTP to a running server (default: the
    repo's read-only DB daemon at http://127.0.0.1:8765/mcp), or a
    spawned ``python -m llm_agents.mcp --stdio`` subprocess when
    ``stdio_command`` is given instead of a URL;
  * ``tool_schemas()`` — the server's tool list converted to
    chat-completion tool declarations (MCP's ``inputSchema`` is a
    JSON-Schema dict, the same shape the ``function.parameters`` slot
    expects, so the mapping is 1:1);
  * ``call_tool()`` — one tool call, result flattened to the text the
    LLM reads back (text blocks joined; ``structuredContent`` JSON
    when the server sent it; ``isError`` results surfaced as
    ``MCP tool error: …`` text so the loop can recover instead of
    crashing).
"""
from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from types import TracebackType
from typing import Any, Dict, Optional, Sequence

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

# llm_agents/deep_agents/core/mcp.py → up 3 = the repo root (the
# package now lives three levels deep; parents[2] pointed at
# llm_agents/ itself, which broke `python -m` stdio spawns — the
# module was unfindable from that cwd).
_REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_MCP_URL = "http://127.0.0.1:8765/mcp"


class McpToolClient:
    """One MCP client session as an async context manager."""

    def __init__(
        self, *,
        url: Optional[str] = None,
        stdio_command: Optional[Sequence[str]] = None,
    ) -> None:
        """Connect by *url* (streamable HTTP), or spawn *stdio_command*.

        Exactly one of the two: a ``stdio_command`` (e.g.
        ``[sys.executable, "-u", "-m", "llm_agents.mcp", "--stdio"]``)
        makes this client own a server subprocess over stdin/stdout;
        a ``url`` attaches to an already-running server.
        """
        if bool(url) == bool(stdio_command):
            raise ValueError("pass exactly one of url / stdio_command")
        self._url = url
        self._stdio_command = list(stdio_command) if stdio_command else None
        self._session: Optional[ClientSession] = None
        self._transport = contextlib.AsyncExitStack()

    # -- lifecycle ------------------------------------------------------

    async def __aenter__(self) -> "McpToolClient":
        if self._url is not None:
            streams = await self._transport.enter_async_context(
                streamable_http_client(self._url))
        else:
            assert self._stdio_command is not None
            streams = await self._transport.enter_async_context(
                stdio_client(StdioServerParameters(
                    command=self._stdio_command[0],
                    args=self._stdio_command[1:],
                    cwd=str(_REPO_ROOT),
                )))
        # Both transports yield (read_stream, write_stream[, extras]) —
        # the first two slots are all ClientSession takes.
        read_stream, write_stream = streams[0], streams[1]
        self._session = await self._transport.enter_async_context(
            ClientSession(read_stream, write_stream))
        await self._session.initialize()
        return self

    async def __aexit__(
        self, exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException], tb: Optional[TracebackType],
    ) -> None:
        await self._transport.aclose()
        self._session = None

    def _live_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("McpToolClient used outside its context")
        return self._session

    # -- the two things the agent loop needs ----------------------------

    async def tool_schemas(self) -> list[Dict[str, Any]]:
        """The server's tools as chat-completion tool declarations."""
        listing = await self._live_session().list_tools()
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.input_schema or {"type": "object"},
                },
            }
            for tool in listing.tools
        ]

    async def call_tool(self, name: str,
                        arguments: Dict[str, Any]) -> str:
        """Call one tool; return its content as the text the LLM reads."""
        result = await self._live_session().call_tool(name, arguments)
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            return json.dumps(structured, ensure_ascii=False, default=str)
        text = "\n".join(
            getattr(block, "text", "")
            for block in (result.content or [])
            if getattr(block, "text", None) is not None
        )
        if getattr(result, "isError", False):
            return f"MCP tool error: {text or name}"
        return text
