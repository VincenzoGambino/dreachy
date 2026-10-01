"""One authenticated MCP session per call chain, from synchronous code (R4).

Backend methods are synchronous (tools call them via asyncio.to_thread) and
the `mcp` SDK is async: run() opens a session in the calling worker thread
with anyio.run, so nothing ever blocks the conversation app's event loop.

One session per run() (R4 Ruling 2): a whole call chain — load, then read
fields; stub, set fields, save — runs inside one. Handles do outlive their
session on the recorded server, so pooling sessions is a later optimisation.

Every tool answers an envelope, {"success", "message", "data"}, and never
sets the MCP error flag: a failure is `success: false`. A token the site
refuses gets 401 with an HTML body from Simple OAuth, which the SDK reports
only as a generic -32603 — so a response hook watches the HTTP status.
(docs/mcp-findings.md "Verified from Dreachy".)

The `mcp` SDK (2.x) is built on httpx2, not httpx: its client, transport and
errors come from there. The Bearer token comes from drupal-api-client's OAuth
handling (R2) — grant, cache, refresh margin — so the secret stays in the
client/auth layer and never reaches a message or the log.
"""

from __future__ import annotations

import html
import json
import logging
import re
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, TypeVar

import anyio
from anyio.from_thread import BlockingPortal, start_blocking_portal
import httpx
import httpx2
from drupal_api_client import ApiClient, AuthenticationError
from mcp import ClientSession
from mcp import types as mcp_types
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from .auth import library_authentication
from .backend import DreachyAuthError, DreachySiteError
from .config import Config

logger = logging.getLogger(__name__)

T = TypeVar("T")

# JSON-RPC code mcp_server answers an unauthenticated request with.
_AUTH_REQUIRED = -32001
_REFUSED = "the site refused Dreachy's MCP login — check the site login on Dreachy's settings page"

HttpClientFactory = Callable[[dict[str, str]], httpx2.AsyncClient]


class McpToolError(DreachySiteError):
    """A tool that answered `success: false` (or, by MCP's own flag, isError)."""


class _Unauthorised(Exception):
    """The MCP server refused the token (retried once with a fresh one)."""


class ToolCaller:
    """What a call chain receives: calls tools within the one open session."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """The envelope's `data`; McpToolError when the tool failed."""
        data, _ = await self.call_with_message(name, arguments)
        return data

    async def call_with_message(self, name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """The envelope's `data` and `message` (list totals, saved ids)."""
        result = await self._session.call_tool(name, arguments)
        if result.is_error:
            text = " ".join(getattr(item, "text", "") for item in result.content)
            raise McpToolError(f"{name}: {_clean(text) or 'the tool reported an error'}")
        envelope = result.structured_content if result.structured_content is not None else _text_json(result)
        if not isinstance(envelope, dict) or "success" not in envelope:
            raise DreachySiteError(f"{name}: the MCP server answered in an unexpected shape")
        message = _clean(str(envelope.get("message") or ""))
        if not envelope["success"]:
            raise McpToolError(f"{name}: {message or 'the tool reported an error'}")
        data = envelope.get("data")
        return (dict(data) if isinstance(data, dict) else {}), message

    async def list_tools(self) -> list[mcp_types.Tool]:
        return list((await self._session.list_tools()).tools)


def _text_json(result: mcp_types.CallToolResult) -> Any:
    for item in result.content:
        if getattr(item, "type", "") == "text":
            try:
                return json.loads(item.text)
            except ValueError:
                return None
    return None


def _clean(text: str) -> str:
    """A server message as plain text: Drupal escapes its placeholders' HTML."""
    return re.sub(r"<[^>]+>", "", html.unescape(text)).strip()[:200]


@dataclass
class _Open:
    """An initialised session: its caller, its HTTP client, the refusals seen."""

    caller: ToolCaller
    http: httpx2.AsyncClient
    refused: list[int]


class McpSession:
    """Runs call chains against the site's MCP server.

    run(): a session of its own per chain, opened and closed in the calling
    thread — for writes (Ruling 2). run_shared(): one long-lived session for
    reads, kept open on a background event loop and reopened when it fails
    (R4 latency ruling; handles outlive sessions on the recorded server).
    """

    def __init__(
        self,
        config: Config,
        *,
        endpoint: str,
        http_client_factory: HttpClientFactory | None = None,
        token_http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.endpoint = endpoint
        # Token handling is drupal-api-client's (R2); None means anonymous.
        self._auth = ApiClient(
            config.base_url,
            authentication=library_authentication(config),
            http_client=token_http_client,
            timeout=config.request_timeout,
        )
        self._factory = http_client_factory or (
            lambda headers: httpx2.AsyncClient(headers=headers, timeout=config.request_timeout, follow_redirects=True)
        )
        self._shared_lock = threading.Lock()
        self._portal_cm: AbstractContextManager[BlockingPortal] | None = None
        self._portal: BlockingPortal | None = None
        self._shared_cm: AbstractContextManager[_Open] | None = None
        self._shared: _Open | None = None

    def close(self) -> None:
        self._drop_shared()
        with self._shared_lock:
            portal_cm, self._portal_cm, self._portal = self._portal_cm, None, None
        if portal_cm is not None:
            portal_cm.__exit__(None, None, None)
        self._auth.close()

    # -- one session per chain ---------------------------------------------

    def run(self, chain: Callable[[ToolCaller], Awaitable[T]]) -> T:
        """Open one session, run *chain* in it, close it. Raises DreachySiteError
        (DreachyAuthError for a refused login) — never a raw transport error."""
        try:
            return anyio.run(self._run, chain)
        except _Unauthorised:
            # The library retries a 401 on its own HTTP requests, not on MCP's:
            # drop the cached token and try once more with a fresh one.
            self._forget_token()
            try:
                return anyio.run(self._run, chain)
            except _Unauthorised as exc:
                raise DreachyAuthError(_REFUSED, status=401) from exc

    def list_tools(self) -> list[mcp_types.Tool]:
        async def chain(caller: ToolCaller) -> list[mcp_types.Tool]:
            return await caller.list_tools()

        return self.run(chain)

    async def _run(self, chain: Callable[[ToolCaller], Awaitable[T]]) -> T:
        refused: list[int] = []
        try:
            async with self._open(self._headers(), refused) as opened:
                return await chain(opened.caller)
        except Exception as exc:  # the SDK wraps failures in (nested) exception groups
            if refused:
                raise _Unauthorised() from exc
            raise _translate(exc) from exc

    # -- the shared read session -------------------------------------------

    def run_shared(self, chain: Callable[[ToolCaller], Awaitable[T]]) -> T:
        """Run a READ chain on the long-lived session. A failure of the
        session itself (gone, unreachable, token refused) reopens it and
        retries the chain once; a tool's own refusal (DreachySiteError from
        the chain) is raised as it is, and the session stays."""
        for attempt in (1, 2):
            refused: list[int] = []
            try:
                headers = self._headers()
                opened = self._shared_session(headers, refused)
                refused = opened.refused
                opened.http.headers.update(headers)  # a token refreshed since it opened
                portal = self._portal
                assert portal is not None
                return portal.call(chain, opened.caller)
            except DreachySiteError:
                raise  # the chain's (or the token endpoint's) own answer
            except Exception as exc:
                error = _Unauthorised() if refused else _translate(exc)
                self._drop_shared()
                if isinstance(error, _Unauthorised):
                    self._forget_token()
                if attempt == 2:
                    if isinstance(error, _Unauthorised):
                        raise DreachyAuthError(_REFUSED, status=401) from exc
                    raise error from exc
        raise AssertionError("unreachable")

    def _shared_session(self, headers: dict[str, str], refused: list[int]) -> _Open:
        with self._shared_lock:
            if self._shared is not None:
                return self._shared
            if self._portal is None:
                self._portal_cm = start_blocking_portal()
                self._portal = self._portal_cm.__enter__()
            # Entered and exited in one task of the portal's loop, as the
            # SDK's task groups require.
            cm = self._portal.wrap_async_context_manager(self._open(headers, refused))
            self._shared = cm.__enter__()
            self._shared_cm = cm
            return self._shared

    def _drop_shared(self) -> None:
        with self._shared_lock:
            cm, self._shared_cm, self._shared = self._shared_cm, None, None
        if cm is None:
            return
        try:
            cm.__exit__(None, None, None)
        except Exception as exc:  # a session that already failed may fail closing too
            logger.debug("Closing the shared MCP session failed: %s", type(exc).__name__)

    # -- shared plumbing ---------------------------------------------------

    @asynccontextmanager
    async def _open(self, headers: dict[str, str], refused: list[int]) -> AsyncIterator[_Open]:
        async def watch(response: httpx2.Response) -> None:
            if response.status_code in (401, 403):
                refused.append(response.status_code)

        async with self._factory(headers) as http:
            http.event_hooks["response"].append(watch)
            async with streamable_http_client(self.endpoint, http_client=http) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield _Open(ToolCaller(session), http, refused)

    def _forget_token(self) -> None:
        self._auth._oauth_token_response = None  # noqa: SLF001 — plan R4 divergence 2

    def _headers(self) -> dict[str, str]:
        try:
            return self._auth.add_authorization_header()
        except AuthenticationError as exc:
            raise DreachyAuthError(_REFUSED) from exc
        except httpx.HTTPError as exc:
            raise DreachySiteError(f"the site's token endpoint can't be reached ({type(exc).__name__})") from exc


def _leaf(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _translate(exc: Exception) -> Exception:
    """The SDK's failure as Dreachy's error. Messages carry no credentials."""
    leaf = _leaf(exc)
    if isinstance(leaf, (_Unauthorised, DreachySiteError)):
        return leaf
    if isinstance(leaf, MCPError):
        if leaf.code == _AUTH_REQUIRED:
            return _Unauthorised()
        return DreachySiteError(f"the MCP server refused the request: {leaf.message[:200]}")
    if isinstance(leaf, httpx2.HTTPStatusError):
        status = leaf.response.status_code
        if status in (401, 403):
            return _Unauthorised()
        return DreachySiteError(f"the MCP server answered {status}", status=status)
    if isinstance(leaf, (httpx2.HTTPError, OSError)):
        return DreachySiteError(f"the MCP server can't be reached ({type(leaf).__name__})")
    return DreachySiteError(f"unexpected MCP failure ({type(leaf).__name__})")
