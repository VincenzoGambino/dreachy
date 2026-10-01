"""McpBackend — the Backend (backend.py) over the site's MCP server (R4).

Same interface and node dicts as JsonApiBackend; the calls are the site's
MCP tools, found by mcp_mapping.py. What the recorded server allows shapes
every read (docs/mcp-findings.md "Verified from Dreachy"):

- Reads share one long-lived session (McpSession.run_shared), reopened when
  it fails. Writes get a session each (Task 6).
- entity_list returns ONE requested field per call, for every listed item,
  and has no status filter. So a read lists a window of the newest content
  once per field it needs — at most `mcp_concurrency` calls at a time, the
  sandbox's safe limit — and joins them by id: the cost doesn't grow with
  the number of items, and nothing is loaded one by one.
- Search hits don't carry status or dates, so each hit kept is loaded and
  read in full (one load, one all-fields call).
- There's no lookup by path or title: get_article searches (Ruling 3).
- Bundles come from one listing of all content, or the installer's list
  (Ruling A): a type with no content yet is invisible.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

import anyio
import httpx
from mcp.shared.exceptions import MCPError

from .backend import Backend, DreachySiteError, SiteAction
from .client import _strip_html
from .config import Config
from .mcp_chain import Handle, field_values, load, save, set_value, stub
from .mcp_mapping import McpMapping, discover_mapping, is_denied, is_entity_write
from .mcp_session import HttpClientFactory, McpSession, McpToolError, ToolCaller, _leaf
from .schema import Schema, TypeSchema, humanize, type_schema_from_definitions

logger = logging.getLogger(__name__)

T = TypeVar("T")

_SUMMARY_FALLBACK_CHARS = 200
_ARCHIVED_STATE = "archived"
_DRAFT_STATE = "draft"
# As over JSON:API: core's fallback format, usable by every role (the
# sandbox refused basic_html).
_NOTE_TEXT_FORMAT = "plain_text"
# The SDK's code for an HTTP error answer; the sandbox's 503 under load.
_SERVER_ERROR = -32603
_HIT_ID = re.compile(r"node/(\d+)")
# Fields every node dict needs, besides the type's text fields.
_NODE_FIELDS = ("status", "moderation_state", "created", "changed", "path")
_NO_INDEX = "no MCP search index is set — choose one on Dreachy's settings page"
# A site action's answer is read by the model: a preview past this many characters.
_ACTION_RESULT_CHARS = 4000
_NOT_AN_ACTION = "that site action isn't available"


def _iso(timestamp: Any) -> str | None:
    """The server's unix-timestamp string as ISO 8601 (what the tools expect)."""
    try:
        return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("processed") or value.get("value")
    return _strip_html(value) if isinstance(value, str) else ""


def _published(value: Any) -> bool:
    # A missing status fails closed: Dreachy is always logged in over MCP,
    # and may see drafts (JsonApiBackend.get_article's rule).
    return value in (True, 1, "1", "true")


def node_dict(meta: dict[str, Any], values: dict[str, Any], type_schema: TypeSchema) -> dict[str, Any]:
    """The Backend node dict from listing metadata and field values."""
    body = next((text for name in type_schema.text_fields if (text := _text(values.get(name)))), "")
    summary = _text(values.get(type_schema.summary_field)) if type_schema.summary_field else ""
    path = values.get("path")
    return {
        "id": values.get("uuid") or meta.get("uuid") or meta.get("id"),
        "title": values.get("title") or meta.get("label"),
        "type": meta.get("bundle"),
        "created": _iso(values.get("created")),
        "changed": _iso(values.get("changed")),
        "path": path.get("alias") if isinstance(path, dict) else None,
        "body": body,
        "summary": summary or body[:_SUMMARY_FALLBACK_CHARS],
        "status": _published(values.get("status")),
        "moderation_state": values.get("moderation_state"),
    }


async def _call_retrying(caller: ToolCaller, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """One call, retried once if the server answered with an HTTP error (503)."""
    try:
        return await caller.call(name, arguments)
    except MCPError as exc:
        if exc.code != _SERVER_ERROR:
            raise
        await anyio.sleep(0.5)
        return await caller.call(name, arguments)


async def _gather(limit: int, jobs: list[Callable[[], Awaitable[T]]]) -> list[T]:
    """Run *jobs* at most *limit* at a time; the first failure is raised as itself."""
    results: list[Any] = [None] * len(jobs)
    semaphore = anyio.Semaphore(max(1, limit))

    async def run(i: int, job: Callable[[], Awaitable[T]]) -> None:
        async with semaphore:
            results[i] = await job()

    try:
        async with anyio.create_task_group() as group:
            for i, job in enumerate(jobs):
                group.start_soon(run, i, job)
    except BaseExceptionGroup as group_error:
        raise _leaf(group_error) from None
    return results


class McpBackend(Backend):
    """The Backend over the site's MCP server. Needs the site login (R2)."""

    def __init__(
        self,
        config: Config,
        *,
        http_client_factory: HttpClientFactory | None = None,
        token_http_client: httpx.Client | None = None,
        auto_discover: bool = False,
    ) -> None:
        super().__init__(config, auto_discover=auto_discover)
        self._session = McpSession(
            config,
            endpoint=config.effective_mcp_endpoint,
            http_client_factory=http_client_factory,
            token_http_client=token_http_client,
        )
        self._mapping: McpMapping | None = None
        self._tools: dict[str, Any] = {}
        self._mapping_lock = threading.Lock()
        self._actions: dict[str, SiteAction] | None = None

    def close(self) -> None:
        self._session.close()

    # -- plumbing ----------------------------------------------------------

    async def _mapped(self, caller: ToolCaller) -> McpMapping:
        if self._mapping is None:
            tools = await caller.list_tools()
            mapping = discover_mapping(tools, self.config.mcp_mapping, search_index=self.config.mcp_search_index)
            with self._mapping_lock:
                self._tools = self._tools or {tool.name: tool for tool in tools}
                self._mapping = self._mapping or mapping
        return self._mapping

    def _read(self, chain: Callable[[ToolCaller, McpMapping], Awaitable[T]]) -> T:
        async def run(caller: ToolCaller) -> T:
            return await chain(caller, await self._mapped(caller))

        return self._session.run_shared(run)

    async def _window(
        self, caller: ToolCaller, mapping: McpMapping, types: Schema, *, sort: str, amount: int
    ) -> list[dict[str, Any]]:
        """Node dicts for the newest *amount* nodes by *sort*, enabled types
        only: one list call per field, joined by id."""
        text_fields = {name for t in types.values() for name in (*t.text_fields, t.summary_field) if name}
        fields = [*_NODE_FIELDS, *sorted(text_fields)]
        lists = await _gather(
            self.config.mcp_concurrency,
            [
                (lambda f=f: _call_retrying(caller, mapping.entity_list, mapping.list_args("node", amount=amount, sort=sort, field=f)))
                for f in fields
            ],
        )
        # Only items the status list saw: one that appeared between the calls
        # (or moved at the window's edge) has no status, and mustn't pass for
        # unpublished — fields[0] is "status".
        listed_status = {str((item.get("_metadata") or {}).get("id")) for item in lists[0].get("results") or []}
        items: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for listed in lists:
            for item in listed.get("results") or []:
                meta = item.get("_metadata") or {}
                if str(meta.get("id")) not in listed_status:
                    continue
                _, values = items.setdefault(str(meta.get("id")), (meta, {}))
                values.update({k: v for k, v in item.items() if k != "_metadata"})
        return [node_dict(meta, values, types[meta.get("bundle")]) for meta, values in items.values() if meta.get("bundle") in types]

    async def _read_hit(self, caller: ToolCaller, mapping: McpMapping, nid: int, types: Schema) -> dict[str, Any] | None:
        handle = await load(caller, mapping, "node", nid)
        bundle = handle.metadata.get("bundle")
        if bundle not in types:
            return None
        values = await field_values(caller, mapping, handle)
        return node_dict({"bundle": bundle, "id": str(nid)}, values, types[bundle])

    async def _search(self, caller: ToolCaller, mapping: McpMapping, words: str, limit: int) -> list[tuple[int, dict[str, Any]]]:
        """(nid, hit) per node, best first: chunks and translations deduped."""
        if not mapping.search_index:
            raise DreachySiteError(_NO_INDEX)
        data = await caller.call(mapping.search, mapping.search_args(words, limit=limit))
        seen: dict[int, dict[str, Any]] = {}
        for hit in data.get("results") or []:
            found = _HIT_ID.search(str(hit.get("id", "")))
            if found:
                seen.setdefault(int(found.group(1)), hit)
        return list(seen.items())

    @staticmethod
    def _hit_type(hit: dict[str, Any]) -> str | None:
        values = ((hit.get("fields") or {}).get("type") or {}).get("values") or []
        return str(values[0]) if values else None

    # -- content model -----------------------------------------------------

    def get_schema(self) -> Schema:
        async def chain(caller: ToolCaller, mapping: McpMapping) -> Schema:
            if self.config.mcp_bundles:
                moderated = dict.fromkeys(self.config.mcp_bundles, False)
            else:
                listed = await caller.call(mapping.entity_list, mapping.list_args("node", amount=0, field="moderation_state"))
                moderated = {}
                for item in listed.get("results") or []:
                    bundle = (item.get("_metadata") or {}).get("bundle")
                    if bundle:
                        moderated[bundle] = moderated.get(bundle, False) or item.get("moderation_state") is not None
            bundles = sorted(moderated)

            async def definitions(bundle: str) -> dict[str, Any] | None:
                try:
                    return await _call_retrying(caller, mapping.field_definitions, mapping.definitions_args("node", bundle))
                except McpToolError as exc:
                    logger.info("Can't read node--%s's fields over MCP, skipping it: %s", bundle, exc)
                    return None

            found = await _gather(self.config.mcp_concurrency, [(lambda b=b: definitions(b)) for b in bundles])
            schema: Schema = {}
            for bundle, data in zip(bundles, found):
                if data is None:
                    continue
                type_schema = type_schema_from_definitions(
                    humanize(bundle), data.get("field_definitions") or {}, moderated=moderated[bundle]
                )
                if type_schema is None:
                    logger.info("Skipping node--%s: it has no formatted text field", bundle)
                else:
                    schema[bundle] = type_schema
            return schema

        return self._read(chain)

    # -- queries -----------------------------------------------------------

    def get_recent_nodes(self, limit: int | None = None, *, include_unpublished: bool = False) -> list[dict[str, Any]]:
        limit = limit or self.config.whats_new_limit
        types = self._types()
        # The watcher polls with limit=1: a small window keeps that cheap.
        amount = min(self.config.mcp_window, max(10, limit * 5))

        async def chain(caller: ToolCaller, mapping: McpMapping) -> list[dict[str, Any]]:
            return await self._window(caller, mapping, types, sort="created", amount=amount)

        nodes = [n for n in self._read(chain) if n["status"] or include_unpublished]
        nodes.sort(key=lambda n: n["created"] or "", reverse=True)
        return nodes[:limit]

    def find_content(
        self, keyword: str, content_type: str | None = None, *, include_unpublished: bool = False
    ) -> list[dict[str, Any]]:
        """Semantic search, best match first (not newest first, as over JSON:API)."""
        limit = self.config.find_content_limit
        types = self._types()
        if content_type in types:
            types = {content_type: types[content_type]}

        async def chain(caller: ToolCaller, mapping: McpMapping) -> list[dict[str, Any]]:
            hits = await self._search(caller, mapping, keyword, limit * 3)
            wanted = [nid for nid, hit in hits if self._hit_type(hit) in (None, *types)][:limit]
            nodes = await _gather(
                self.config.mcp_concurrency, [(lambda nid=nid: self._read_hit(caller, mapping, nid, types)) for nid in wanted]
            )
            return [n for n in nodes if n is not None and (n["status"] or include_unpublished)]

        return self._read(chain)

    def get_article(self, title_or_path: str, *, include_unpublished: bool = False) -> dict[str, Any] | None:
        """No lookup by path or title over MCP: a path's words are searched,
        and an exact title among the hits wins (Ruling 3). Otherwise only a
        hit whose title has a word asked for: never just the nearest one."""
        types = self._types()
        words = title_or_path.strip()
        if words.startswith("/"):
            words = " ".join(re.split(r"[/\-_]+", words)).strip()

        async def chain(caller: ToolCaller, mapping: McpMapping) -> dict[str, Any] | None:
            hits = [(nid, hit) for nid, hit in await self._search(caller, mapping, words, 10) if self._hit_type(hit) in (None, *types)]
            exact = [(nid, hit) for nid, hit in hits if str(hit.get("label", "")).casefold() == words.casefold()]
            # Semantic search always has a nearest hit: without an exact
            # title, read one only if its title has a word asked for.
            named = [(nid, hit) for nid, hit in hits if _names_it(str(hit.get("label", "")), words)]
            for nid, _ in (exact or named)[:3]:
                node = await self._read_hit(caller, mapping, nid, types)
                if node is not None and (node["status"] or include_unpublished):
                    return node
            return None

        return self._read(chain)

    # -- editorial ---------------------------------------------------------

    def can_edit(self) -> bool:
        """Logged in, a token granted, and the server offers the write tools."""
        if not self.config.uses_oauth:
            return False

        async def chain(caller: ToolCaller, mapping: McpMapping) -> bool:
            return mapping.can_write

        try:
            return self._read(chain)
        except DreachySiteError as exc:
            # The type only: an exception's text is no place to risk credentials.
            logger.warning("Editing is unavailable over MCP (%s)", type(exc).__name__)
            return False

    def get_pending_nodes(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Pending among the most recently changed ~20 items (Ruling 4): the
        server can't filter on status, but lists it per item in one call."""
        limit = limit or self.config.pending_sample_limit
        types = self._types()
        amount = min(limit, self.config.mcp_pending_window)

        async def chain(caller: ToolCaller, mapping: McpMapping) -> list[dict[str, Any]]:
            return await self._window(caller, mapping, types, sort="changed", amount=amount)

        nodes = [n for n in self._read(chain) if not n["status"] and n["moderation_state"] != _ARCHIVED_STATE]
        nodes.sort(key=lambda n: n["changed"] or "", reverse=True)
        return nodes[:limit]

    def create_draft(self, content_type: str, title: str, body: str) -> dict[str, Any]:
        """One unpublished node, in a session of its own (Ruling 2).

        Draft-only is checked BEFORE saving (Ruling B): the stub's moderation
        state is read and set to draft if it isn't — if that's refused,
        nothing is saved — and an unmoderated stub must be unpublished. The
        saved node is re-read after, and a published one is an error.
        Required text fields get the note's text; Dreachy invents no other
        value, so a field the site won't fill shows in the refusal (Ruling C).
        """
        # As over JSON:API: only a discovered schema says which field holds
        # the text, and _types() first retries a failed discovery when due.
        types = self._types()
        type_schema = types.get(content_type) if self.schema_discovered else None
        if type_schema is None:
            raise DreachySiteError(f"notes can't be saved as {content_type!r} on this site right now")

        async def chain(caller: ToolCaller) -> dict[str, Any]:
            mapping = await self._mapped(caller)
            if not mapping.can_write:
                raise DreachySiteError("this site's MCP server offers no way to save content")
            handle = await stub(caller, mapping, "node", content_type, {"title": title, "status": False})
            handle = await self._make_draft(caller, mapping, handle)
            for name in (type_schema.text_fields[0], *type_schema.required_text):
                value = {"value": body, "format": _NOTE_TEXT_FORMAT} if name == type_schema.text_fields[0] else {"value": body}
                try:
                    handle = await set_value(caller, mapping, handle, name, value)
                except McpToolError as exc:
                    raise DreachySiteError(f"the site wouldn't take the note's text in {name}, so nothing was saved ({_reason(exc)})") from exc
            try:
                saved = await save(caller, mapping, handle)
            except McpToolError as exc:
                raise _save_refused(exc, type_schema) from exc
            reread = await load(caller, mapping, "node", saved["id"])
            values = await field_values(caller, mapping, reread)
            return node_dict({"bundle": content_type, "id": saved["id"]}, values, type_schema), values

        node, values = self._session.run(chain)
        if "status" not in values:
            # The safety check needs the answer: no status is no proof of a draft.
            logger.error("drupal_create_note: can't read %s's status after saving it", node["id"])
            raise DreachySiteError(
                "the site saved the note but wouldn't say it's unpublished — check it on the site"
            )
        if node["status"] or node["moderation_state"] == "published":
            logger.error("drupal_create_note: the site published %s despite a draft request", node["id"])
            raise DreachySiteError(
                "the site published the note instead of keeping it as a draft — tell whoever looks after the site"
            )
        return node

    async def _make_draft(self, caller: ToolCaller, mapping: McpMapping, handle: Handle) -> Handle:
        state = (await field_values(caller, mapping, handle, "moderation_state")).get("moderation_state")
        if state == _DRAFT_STATE:
            return handle
        if state is None:  # unmoderated: status decides, and the stub asked for unpublished
            if not _published((await field_values(caller, mapping, handle, "status")).get("status")):
                return handle
            field, value = "status", {"value": False}
        else:
            field, value = "moderation_state", {"value": _DRAFT_STATE}
        try:
            return await set_value(caller, mapping, handle, field, value)
        except McpToolError as exc:
            raise DreachySiteError(f"the site wouldn't let the note be a draft, so nothing was saved ({_reason(exc)})") from exc


    # -- site actions (Task 8) ---------------------------------------------

    def site_actions(self) -> dict[str, SiteAction]:
        """DREACHY_MCP_EXTRA_TOOLS ∩ what the server offers ∖ the deny-list.
        Found once (a denied or missing entry is logged once) and kept."""
        if self._actions is not None:
            return dict(self._actions)
        if not self.config.mcp_extra_tools:
            self._actions = {}
            return {}

        async def chain(caller: ToolCaller, mapping: McpMapping) -> dict[str, Any]:
            return self._tools

        served = self._read(chain)
        actions: dict[str, SiteAction] = {}
        mapping = self._mapping
        writes = {mapping.stub, mapping.set_value, mapping.save} if mapping else set()
        for name in self.config.mcp_extra_tools:
            tool = served.get(name)
            if tool is None:
                logger.warning("Site action %s isn't offered by this site's MCP server; ignoring it", name)
            elif name in writes or is_entity_write(name):
                logger.warning("Site action %s is only used for notes, through their draft checks; ignoring it", name)
            elif is_denied(tool):
                logger.warning("Site action %s is never allowed (Dreachy never publishes, deletes or discards)", name)
            else:
                properties = (tool.input_schema or {}).get("properties") or {}
                actions[name] = SiteAction(
                    name,
                    tool.description or "",
                    bool(tool.annotations and tool.annotations.read_only_hint is True),
                    tuple(properties),
                )
        self._actions = actions
        return dict(actions)

    def known_site_actions(self) -> dict[str, SiteAction]:
        return dict(self._actions or {})

    def run_site_action(self, name: str, arguments: dict[str, Any], *, confirmed: bool) -> dict[str, Any]:
        """Run one allowlisted extra. A write needs confirmed (the tool asks
        first; this is the second line) and gets a session of its own."""
        action = self.site_actions().get(name)
        if action is None:
            raise DreachySiteError(_NOT_AN_ACTION)
        if not action.read_only and confirmed is not True:
            raise DreachySiteError("a site action that changes the site needs the person's spoken yes first")
        tool = self._tools[name]
        args = _safe_arguments(tool, arguments)

        async def run(caller: ToolCaller) -> tuple[dict[str, Any], str]:
            return await caller.call_with_message(name, args)

        if action.read_only:
            data, message = self._read(lambda caller, mapping: run(caller))
        else:
            data, message = self._session.run(run)
        text = json.dumps(data, ensure_ascii=False, default=str)
        if len(text) > _ACTION_RESULT_CHARS:
            return {"message": message, "result_preview": text[:_ACTION_RESULT_CHARS]}
        return {"message": message, "result": data}


def _names_it(label: str, words: str) -> bool:
    """Whether a title contains a significant word (4+ letters) of a request."""
    significant = {w for w in re.findall(r"\w+", words.casefold()) if len(w) >= 4}
    return any(w in label.casefold() for w in significant)


def _reason(exc: McpToolError) -> str:
    """The server's own words, without the tool name McpToolError puts first."""
    return str(exc).split(": ", 1)[-1]


def _save_refused(exc: McpToolError, type_schema: TypeSchema) -> DreachySiteError:
    reason = _reason(exc).removeprefix("Entity validation failed: ")
    named = next((name for name in type_schema.required_other if name in reason), None)
    if named:
        return DreachySiteError(
            f"the site needs {named} filled in for a {type_schema.label.lower()}, which I can't do from a dictated "
            f"note — nothing was saved"
        )
    return DreachySiteError(f"the site rejected the note, so nothing was saved ({reason})")



def _safe_arguments(tool: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """check_access is always TRUE, and nothing is ever asked to go live:
    `published` is never sent true, and is sent false where the tool says it
    defaults to true (canvas_create_page)."""
    properties = (tool.input_schema or {}).get("properties") or {}
    args = {k: v for k, v in arguments.items() if k != "check_access"}
    if "check_access" in properties:
        args["check_access"] = True
    published = properties.get("published")
    if published is not None:
        defaults_live = "defaults to true" in str(published.get("description", "")).lower()
        value = args.pop("published", None)  # null counts as unset: a server may read it as its default
        if value is not None or defaults_live:
            args["published"] = False
    return args
