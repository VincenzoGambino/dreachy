"""An in-memory MCP server for McpBackend tests (R4).

Speaks JSON-RPC 2.0 over streamable HTTP, as the official `mcp` SDK client
does it (spiked 2026-10-01): POST `initialize` (answered with an
`mcp-session-id` header), the `initialized` notification (202), an optional
GET for a server stream (declined, 405), `tools/call` and `tools/list` POSTs,
and a DELETE on close.

Answers like the sandbox's `mcp_server` 1.0.0 as recorded in R4 Task 1
(tests/fixtures/mcp/, docs/mcp-findings.md "Verified from Dreachy"):

- every tool result is the envelope `{"success", "message", "data"}`, sent
  as JSON text AND as structuredContent; a failed tool is `success: false`
  with `isError: false`;
- an unknown tool is a JSON-RPC error;
- no token: 401, realm `mcp_server`, JSON-RPC -32001; a wrong token: 401
  with an HTML body from Simple OAuth (realm `OAuth`).
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import httpx2


class FakeToolError(Exception):
    """Raise from a fake tool to answer `success: false` with this message."""


@dataclass
class Reply:
    """A tool's answer when the message matters (list totals, save ids)."""

    data: Any
    message: str = ""


@dataclass
class Raw:
    """A recorded tools/call result, answered verbatim."""

    result: dict[str, Any]


ToolFn = Callable[[dict[str, Any], dict[str, Any]], Any]


class FakeMcpSite:
    def __init__(
        self,
        tools: dict[str, ToolFn],
        *,
        token: str | None = "good-token",
        annotations: dict[str, dict[str, Any]] | None = None,
        down: bool = False,
    ) -> None:
        self.tools = tools
        self.token = token  # None: no auth required
        self.annotations = annotations or {}
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
        if self.token is not None and auth is None:
            return httpx2.Response(
                401,
                json={"jsonrpc": "2.0", "error": {"code": -32001, "message": "Authentication required"}, "id": None},
                headers={"www-authenticate": 'Bearer realm="mcp_server"'},
            )
        if self.token is not None and auth != f"Bearer {self.token}":
            return httpx2.Response(  # Simple OAuth refuses the token before mcp_server sees it
                401,
                text="<!DOCTYPE html><html><body>The resource owner or authorization server denied the request.</body></html>",
                headers={
                    "content-type": "text/html; charset=UTF-8",
                    "www-authenticate": 'Bearer realm="OAuth", error="access_denied"',
                },
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
            # MCP: a server that no longer knows a session answers 404.
            return httpx2.Response(404, json={"jsonrpc": "2.0", "id": request_id, "error": {"code": -32600, "message": "Session not found"}})
        if method == "tools/list":
            return _result(request_id, {"tools": [self._describe(name) for name in self.tools]})
        if method == "tools/call":
            name = message["params"]["name"]
            arguments = message["params"].get("arguments") or {}
            self.calls.append((session_id, name, arguments))
            if name not in self.tools:
                return _error(request_id, -32602, f'Tool not found: "{name}".')
            try:
                answer = self.tools[name](arguments, self.sessions[session_id])
            except FakeToolError as exc:
                return _result(request_id, _envelope(False, str(exc), []))
            if isinstance(answer, Raw):
                return _result(request_id, answer.result)
            reply = answer if isinstance(answer, Reply) else Reply(answer)
            return _result(request_id, _envelope(True, reply.message, reply.data))
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

    def forget_sessions(self) -> None:
        """As a server restart or session expiry would."""
        self.sessions.clear()


class FakeTokenEndpoint:
    """POST /oauth/token for drupal-api-client (sync httpx): grants `token`, or refuses."""

    def __init__(self, token: str = "good-token", *, accept: bool = True, expires_in: int = 300, rotate: bool = False) -> None:
        self.token = token
        self.accept = accept
        self.expires_in = expires_in  # under the 60 s refresh margin, every request refreshes
        self.rotate = rotate  # a new token per grant: token-1, token-2, …
        self.grants = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/oauth/token" or not self.accept:
            return httpx.Response(401, json={"error": "invalid_client"})
        self.grants += 1
        token = f"{self.token}-{self.grants}" if self.rotate else self.token
        return httpx.Response(200, json={"access_token": token, "expires_in": self.expires_in, "token_type": "Bearer"})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))


def _envelope(success: bool, message: str, data: Any) -> dict[str, Any]:
    body = {"success": success, "message": message, "data": data}
    return {"content": [{"type": "text", "text": json.dumps(body, indent=4)}], "structuredContent": body, "isError": False}


def _result(request_id: Any, result: dict[str, Any], headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(200, json={"jsonrpc": "2.0", "id": request_id, "result": result}, headers=headers)


def _error(request_id: Any, code: int, message: str) -> httpx2.Response:
    return httpx2.Response(200, json={"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def handle_string(token: str, metadata: dict[str, Any]) -> str:
    """How the server hands out a handle: a sentence, with the metadata as JSON."""
    return f"Entity object handle token: {token}. Entity metadata: {json.dumps(metadata, separators=(',', ':'))}"


class FakeEntityStore:
    """The site's entity tools, answering with the recorded names and shapes.

    Handles are global (they outlive their session) and immutable snapshots:
    field_set_value returns a NEW handle with the change, and the old one
    still answers — with the entity as it was before. A chain that keeps
    using an old handle silently loses its earlier changes.
    """

    def __init__(self, nodes: dict[int, dict[str, Any]] | None = None, *, prefix: str = "tool_api__demo_") -> None:
        self.nodes: dict[int, dict[str, Any]] = dict(nodes or {})  # nid -> {"bundle", "fields": {...}}
        self.saves: list[dict[str, Any]] = []
        self.handles: dict[str, dict[str, Any]] = {}
        self.prefix = prefix
        self._handle_ids = itertools.count(1)

    def tools(self) -> dict[str, ToolFn]:
        p = self.prefix
        return {
            f"{p}entity_load_by_id": self._load,
            f"{p}entity_field_values": self._values,
            f"{p}entity_stub": self._stub,
            f"{p}field_set_value": self._set,
            f"{p}entity_save": self._save,
        }

    def _issue(self, entity: dict[str, Any]) -> str:
        token = f"{{{{entity:{next(self._handle_ids):06x}}}}}"
        self.handles[token] = entity
        return handle_string(
            token,
            {
                "entity_type": entity["type"],
                "bundle": entity["bundle"],
                "id": str(entity["id"]) if entity.get("id") else "new",
                "langcode": "en",
                "revision_id": str(entity["id"]) if entity.get("id") else None,
            },
        )

    def _resolve(self, token: Any) -> dict[str, Any]:
        entity = self.handles.get(token)
        if entity is None:
            raise FakeToolError("Tool plugin access denied.")
        return entity

    def _load(self, arguments: dict[str, Any], state: dict[str, Any]) -> Reply:
        nid = arguments["entity_id"]
        if nid not in self.nodes:
            raise FakeToolError("Tool plugin access denied.")  # the real server can't tell missing from forbidden
        node = self.nodes[nid]
        entity = {"type": arguments["entity_type_id"], "bundle": node["bundle"], "id": nid, "fields": dict(node["fields"])}
        return Reply({"loaded_entity": self._issue(entity)}, f"Successfully loaded node entity with ID {nid}")

    def _values(self, arguments: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        fields = dict(self._resolve(arguments["entity"])["fields"])
        wanted = arguments.get("fields")
        if wanted:
            fields = {wanted: fields.get(wanted)} if "," not in wanted else {}
        return {"field_values": fields}

    def _stub(self, arguments: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        base = dict(arguments.get("base_fields") or {})
        entity = {"type": arguments["entity_type_id"], "bundle": arguments["bundle"], "fields": base}
        return {"created_entity": self._issue(entity)}

    def _set(self, arguments: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        value = arguments["value"]
        if not isinstance(value, dict):
            raise FakeToolError("Invalid type. Expected `object`")
        entity = self._resolve(arguments["entity"])
        stored = value.get("value") if set(value) <= {"value", "format"} else value
        updated = {**entity, "fields": {**entity["fields"], arguments["field_name"]: stored}}
        return {"updated_entity": self._issue(updated)}  # the old handle keeps the old snapshot

    def _save(self, arguments: dict[str, Any], state: dict[str, Any]) -> Reply:
        entity = self._resolve(arguments["entity"])
        nid = entity.get("id") or (max(self.nodes, default=0) + 1)
        self.nodes[nid] = {"bundle": entity["bundle"], "fields": dict(entity["fields"])}
        self.saves.append({"id": nid, **entity})
        saved = {**entity, "id": nid}
        return Reply({"saved_entity": self._issue(saved)}, f"Successfully created node entity with ID {nid}")
