"""An in-memory MCP server for McpBackend tests (R4).

Speaks JSON-RPC 2.0 over streamable HTTP, as the official `mcp` SDK client
does it (spiked 2026-10-01): POST `initialize` (answered with an
`mcp-session-id` header), the `initialized` notification (202), an optional
GET for a server stream (declined, 405), `tools/call` and `tools/list` POSTs,
and a DELETE on close. Bearer auth like the real `mcp_server` module: a
missing or wrong token gets 401 with its realm header and JSON-RPC -32001.

State is per session, so handle tokens are session-scoped by construction —
what docs/mcp-findings.md expects of the real server.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from typing import Any

import httpx
import httpx2


class FakeToolError(Exception):
    """Raise from a fake tool to answer with isError: true and this message."""


ToolFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


class FakeMcpSite:
    def __init__(
        self,
        tools: dict[str, ToolFn],
        *,
        token: str | None = "good-token",
        annotations: dict[str, dict[str, Any]] | None = None,
        structured: bool = False,
        down: bool = False,
    ) -> None:
        self.tools = tools
        self.token = token  # None: no auth required
        self.annotations = annotations or {}
        self.structured = structured  # answer with structuredContent instead of JSON text
        self.down = down
        self.sessions: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []  # (session id, tool, arguments)
        self.seen_auth: list[str | None] = []
        self._ids = itertools.count(1)

    # -- HTTP --------------------------------------------------------------

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if self.down:
            raise httpx2.ConnectError("connection refused", request=request)
        auth = request.headers.get("authorization")
        self.seen_auth.append(auth)
        if self.token is not None and auth != f"Bearer {self.token}":
            return httpx2.Response(
                401,
                json={"jsonrpc": "2.0", "error": {"code": -32001, "message": "Authentication required"}, "id": None},
                headers={"www-authenticate": 'Bearer realm="mcp_server"'},
            )
        if request.method == "GET":
            return httpx2.Response(405)
        if request.method == "DELETE":
            self.sessions.pop(request.headers.get("mcp-session-id", ""), None)
            return httpx2.Response(200)
        message = json.loads(request.content)
        if "id" not in message:  # a notification
            return httpx2.Response(202)
        return self._answer(message, request.headers.get("mcp-session-id"))

    def _answer(self, message: dict[str, Any], session_id: str | None) -> httpx2.Response:
        method, request_id = message["method"], message["id"]
        if method == "initialize":
            session_id = f"session-{next(self._ids)}"
            self.sessions[session_id] = {}
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake mcp_server", "version": "0"},
            }
            return _result(request_id, result, headers={"mcp-session-id": session_id})
        if session_id not in self.sessions:
            return _error(request_id, -32600, "unknown session")
        if method == "tools/list":
            return _result(request_id, {"tools": [self._describe(name) for name in self.tools]})
        if method == "tools/call":
            name = message["params"]["name"]
            arguments = message["params"].get("arguments") or {}
            self.calls.append((session_id, name, arguments))
            if name not in self.tools:
                return _error(request_id, -32602, f"Unknown tool: {name}")
            try:
                payload = self.tools[name](arguments, self.sessions[session_id])
            except FakeToolError as exc:
                return _result(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
            if self.structured:
                return _result(request_id, {"content": [], "structuredContent": payload, "isError": False})
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False})
        return _error(request_id, -32601, f"Method not found: {method}")

    def _describe(self, name: str) -> dict[str, Any]:
        tool: dict[str, Any] = {"name": name, "description": name, "inputSchema": {"type": "object"}}
        if name in self.annotations:
            tool["annotations"] = self.annotations[name]
        return tool

    # -- clients -----------------------------------------------------------

    def client_factory(self) -> Callable[[dict[str, str]], httpx2.AsyncClient]:
        """What McpSession takes: headers in, an httpx2 client on this fake out."""
        return lambda headers: httpx2.AsyncClient(transport=httpx2.MockTransport(self.handle), headers=headers)

    def session_ids(self) -> list[str]:
        return [session_id for session_id, _, _ in self.calls]


class FakeTokenEndpoint:
    """POST /oauth/token for drupal-api-client (sync httpx): grants `token`, or refuses."""

    def __init__(self, token: str = "good-token", *, accept: bool = True) -> None:
        self.token = token
        self.accept = accept
        self.grants = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/oauth/token" or not self.accept:
            return httpx.Response(401, json={"error": "invalid_client"})
        self.grants += 1
        return httpx.Response(200, json={"access_token": self.token, "expires_in": 300, "token_type": "Bearer"})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))


def _result(request_id: Any, result: dict[str, Any], headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(200, json={"jsonrpc": "2.0", "id": request_id, "result": result}, headers=headers)


def _error(request_id: Any, code: int, message: str) -> httpx2.Response:
    return httpx2.Response(200, json={"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})
