"""Record what the site's MCP server actually does, from Dreachy's side (R4 Task 1).

Reads the same settings as the app (~/.local/share/dreachy/.env or the
environment): DREACHY_BASE_URL, the OAuth client and DREACHY_OAUTH_SCOPE.
Gets a token through drupal-api-client (R2), then talks to <base>/mcp with
the official `mcp` SDK, saving raw responses under tests/fixtures/mcp/ for
the McpBackend tests to build on.

Phases (run in order; each builds on the last's recordings):
  list   initialize + tools/list: server identity, every tool's schema
  read   read-only calls (entity_list, search, load + field values, field
         definitions, handle reuse across sessions, error shapes, timing)
  read2  second read pass on a known node (5)
  read3  pending by moderation_state, more bundle definitions
  shapes, transitions  NO saves: write-path value shapes, moderation states
  write  ONE unpublished draft: stub -> set fields -> save -> reload
  readback NID  re-read a saved draft's state (no writes)

Access tokens are never written anywhere. Usage (from dreachy-app/):
  uv run python scripts/live/mcp_probe.py <phase> [nid]
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path
from typing import Any

import anyio
import httpx2
from dotenv import load_dotenv
from drupal_api_client import ApiClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from dreachy.auth import library_authentication
from dreachy.config import Config

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "mcp"


def _config() -> Config:
    load_dotenv(Path.home() / ".local" / "share" / "dreachy" / ".env")
    config = Config.from_env()
    if not config.uses_oauth:
        sys.exit("Set the DREACHY_* OAuth settings first.")
    return config


def _token_claims(header: dict[str, str]) -> dict[str, Any]:
    """The access token's JWT claims (scopes, subject) — never the token itself."""
    token = header.get("Authorization", "").removeprefix("Bearer ")
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {"note": "token is not a JWT"}
    return {k: claims.get(k) for k in ("sub", "scope", "scopes", "aud", "client_id", "exp") if k in claims}


def save(name: str, data: Any) -> None:
    path = FIXTURES / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")
    print(f"  saved {path.relative_to(FIXTURES.parents[1])}")


def dump(model: Any) -> Any:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True) if hasattr(model, "model_dump") else model


class Probe:
    def __init__(self) -> None:
        self.config = _config()
        self.endpoint = self.config.base_url.rstrip("/") + "/mcp"
        self.auth = ApiClient(self.config.base_url, authentication=library_authentication(self.config))

    def headers(self) -> dict[str, str]:
        return self.auth.add_authorization_header()

    async def session(self, body) -> Any:
        """Run *body(session)* inside one MCP session; returns its result."""
        async with httpx2.AsyncClient(headers=self.headers(), timeout=60, follow_redirects=True) as http:
            async with streamable_http_client(self.endpoint, http_client=http) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    return await body(session, init)

    async def call(self, session: ClientSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        try:
            result = await session.call_tool(name, arguments)
            out = {"result": dump(result)}
        except Exception as exc:  # recorded, not hidden: error shapes are part of the findings
            out = {"exception": f"{type(exc).__name__}: {exc}"}
        out["seconds"] = round(time.monotonic() - started, 3)
        out["call"] = {"name": name, "arguments": arguments}
        return out


async def phase_list(probe: Probe) -> None:
    print("token claims:", _token_claims(probe.headers()))

    async def body(session: ClientSession, init) -> None:
        save("initialize.json", dump(init))
        tools = (await session.list_tools()).tools
        save("tools_list.json", [dump(t) for t in tools])
        for tool in tools:
            props = (tool.input_schema or {}).get("properties", {})
            required = (tool.input_schema or {}).get("required", [])
            hints = dump(tool.annotations) if tool.annotations else {}
            print(f"- {tool.name}: params {sorted(props)} required {required} annotations {hints}")

    started = time.monotonic()
    await probe.session(body)
    print(f"session (initialize + tools/list): {time.monotonic() - started:.2f}s")


def _tool(suffix: str) -> str:
    tools = json.loads((FIXTURES / "tools_list.json").read_text())
    return next(t["name"] for t in tools if t["name"].endswith(suffix))


def _text_json(recorded: dict[str, Any]) -> Any:
    """The first text content of a recorded result, parsed as JSON if it is."""
    result = recorded.get("result") or {}
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    for item in result.get("content", []):
        if item.get("type") == "text":
            try:
                return json.loads(item["text"])
            except ValueError:
                return item["text"]
    return None


def _entity_metadata(value: Any) -> dict[str, Any]:
    """The JSON metadata after "Entity metadata:" in a handle string."""
    text = json.dumps(value) if not isinstance(value, str) else value
    text = text.replace('\\"', '"')
    start = text.find("Entity metadata: ")
    if start < 0:
        return {}
    try:
        return json.JSONDecoder().raw_decode(text[start + len("Entity metadata: "):])[0]
    except ValueError:
        return {}


def _find_token(value: Any) -> str | None:
    text = json.dumps(value) if not isinstance(value, str) else value
    start = text.find("{{entity:")
    return text[start:text.find("}}", start) + 2] if start >= 0 else None


async def phase_read(probe: Probe) -> None:
    LIST, SEARCH, LOAD = _tool("entity_list"), _tool("search_index"), _tool("entity_load_by_id")
    VALUES, DEFS = _tool("entity_field_values"), _tool("entity_field_value_definitions")
    state: dict[str, Any] = {}

    async def body(session: ClientSession, init) -> None:
        async def rec(name: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
            out = await probe.call(session, tool, args)
            save(f"responses/{name}.json", out)
            return out

        listed = await rec("01-list-articles", LIST, {"entity_type_id": "node", "bundle": "article", "sort_field": "created", "sort_order": "DESC", "amount": 3})
        await rec("02-list-with-fields", LIST, {"entity_type_id": "node", "bundle": "article", "sort_field": "changed", "sort_order": "DESC", "amount": 3, "fields": "title,status,created,changed,moderation_state"})
        await rec("03-list-node-types", LIST, {"entity_type_id": "node_type", "amount": 0})
        await rec("04-search", SEARCH, {"index": "content_vector", "search_words": "admissions", "amount": 5, "check_access": True})
        nid = None
        for match in __import__("re").finditer(r'"id"\s*:\s*"?(\d+)', json.dumps(_text_json(listed))):
            nid = int(match.group(1)); break
        state["nid"] = nid
        loaded = await rec("05-load", LOAD, {"entity_type_id": "node", "entity_id": nid})
        token = _find_token(_text_json(loaded))
        state["token"] = token
        await rec("06-field-values-all", VALUES, {"entity": token})
        await rec("07-field-values-some", VALUES, {"entity": token, "fields": "title,status,moderation_state,created,changed,body"})
        await rec("08-definitions-article", DEFS, {"entity_type_id": "node", "bundle": "article"})
        await rec("09-error-unknown-tool", "tool_api__demo_does_not_exist", {})
        await rec("10-error-admin-only", _tool("system_status"), {})
        await rec("11-error-bad-argument", LOAD, {"entity_type_id": "node", "entity_id": 999999999})

    started = time.monotonic()
    await probe.session(body)
    print(f"read session total: {time.monotonic() - started:.2f}s (nid {state.get('nid')}, token {state.get('token')})")

    async def reuse(session: ClientSession, init) -> None:
        save("responses/12-handle-in-second-session.json", await probe.call(session, VALUES, {"entity": state["token"], "fields": "title"}))

    await probe.session(reuse)

    garbage = {"Authorization": "Bearer not-a-real-token"}
    try:
        async with httpx2.AsyncClient(headers=garbage, timeout=30) as http:
            async with streamable_http_client(probe.endpoint, http_client=http) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
        outcome = "accepted?!"
    except Exception as exc:  # noqa: BLE001 — recording the shape
        leaf = exc
        while isinstance(leaf, BaseExceptionGroup) and leaf.exceptions:
            leaf = leaf.exceptions[0]
        outcome = f"{type(leaf).__module__}.{type(leaf).__name__}: {leaf} (code {getattr(leaf, 'code', None)})"
    save("responses/13-error-garbage-token.json", {"outcome": outcome})


async def phase_read2(probe: Probe) -> None:
    """Second read pass with a known node (id 5, from the search recording)."""
    LIST, LOAD, VALUES = _tool("entity_list"), _tool("entity_load_by_id"), _tool("entity_field_values")
    state: dict[str, Any] = {}

    async def body(session: ClientSession, init) -> None:
        async def rec(name: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
            out = await probe.call(session, tool, args)
            save(f"responses/{name}.json", out)
            return out

        loaded = await rec("05-load", LOAD, {"entity_type_id": "node", "entity_id": 5})
        token = _find_token(_text_json(loaded))
        state["token"] = token
        await rec("06-field-values-all", VALUES, {"entity": token})
        await rec("07-field-values-some", VALUES, {"entity": token, "fields": "title,status,moderation_state,created,changed"})
        await rec("07b-field-values-some-json", VALUES, {"entity": token, "fields": json.dumps(["title", "status", "moderation_state"])})
        await rec("14-list-any-bundle", LIST, {"entity_type_id": "node", "sort_field": "changed", "sort_order": "DESC", "amount": 3})
        await rec("15-list-standard-page", LIST, {"entity_type_id": "node", "bundle": "standard_page", "sort_field": "created", "sort_order": "DESC", "amount": 3})
        await rec("16-list-fields-json", LIST, {"entity_type_id": "node", "sort_field": "changed", "sort_order": "DESC", "amount": 3, "fields": json.dumps(["title", "status", "created"])})
        await rec("17-list-fields-single", LIST, {"entity_type_id": "node", "sort_field": "changed", "sort_order": "DESC", "amount": 3, "fields": "status"})

    started = time.monotonic()
    await probe.session(body)
    print(f"read2 session total: {time.monotonic() - started:.2f}s (token {state.get('token')})")

    async def reuse(session: ClientSession, init) -> None:
        save("responses/12-handle-in-second-session.json", await probe.call(session, VALUES, {"entity": state["token"], "fields": "title"}))

    await probe.session(reuse)


async def phase_read3(probe: Probe) -> None:
    """Pending by status, more bundle definitions, bundles from a full list."""
    LIST, DEFS = _tool("entity_list"), _tool("entity_field_value_definitions")

    async def body(session: ClientSession, init) -> None:
        async def rec(name: str, tool: str, args: dict[str, Any]) -> None:
            save(f"responses/{name}.json", await probe.call(session, tool, args))

        await rec("18-list-fields-moderation-state", LIST, {"entity_type_id": "node", "sort_field": "changed", "sort_order": "DESC", "amount": 20, "fields": "moderation_state"})
        await rec("19-list-sort-status-asc", LIST, {"entity_type_id": "node", "sort_field": "status", "sort_order": "ASC", "amount": 20, "fields": "status"})
        await rec("20-definitions-standard-page", DEFS, {"entity_type_id": "node", "bundle": "standard_page"})
        await rec("21-list-content-moderation-state", LIST, {"entity_type_id": "content_moderation_state", "amount": 5})
        await rec("22-list-all-nodes", LIST, {"entity_type_id": "node", "amount": 0})  # bundle-list source?

    started = time.monotonic()
    await probe.session(body)
    print(f"read3 session total: {time.monotonic() - started:.2f}s")


async def phase_shapes(probe: Probe) -> None:
    """No saves: field_set_value value shapes, default moderation state, handle immutability."""
    STUB, SET, VALUES, LIST, DEFS = (_tool(x) for x in ("entity_stub", "field_set_value", "entity_field_values", "entity_list", "entity_field_value_definitions"))

    async def body(session: ClientSession, init) -> None:
        async def rec(name: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
            out = await probe.call(session, tool, args)
            save(f"responses/{name}.json", out)
            return out

        await rec("40-list-workflows", LIST, {"entity_type_id": "workflow", "amount": 0})
        plain = _find_token(_text_json(await rec("41-stub-title-only", STUB, {"entity_type_id": "node", "bundle": "article", "base_fields": {"title": "Dreachy probe (unsaved)"}})))
        await rec("42-stub-default-state", VALUES, {"entity": plain, "fields": "moderation_state"})
        await rec("42b-stub-default-status", VALUES, {"entity": plain, "fields": "status"})
        await rec("43-set-title", SET, {"entity": plain, "field_name": "title", "value": {"value": "Dreachy probe renamed"}})
        await rec("44-set-body-list-shape", SET, {"entity": plain, "field_name": "body", "value": {"0": {"value": "<p>x</p>", "format": "basic_html"}}})  # PHP reads {"0": ...} as a list
        await rec("44b-set-body-basic-html", SET, {"entity": plain, "field_name": "body", "value": {"value": "<p>x</p>", "format": "basic_html"}})
        await rec("45-set-body-no-format", SET, {"entity": plain, "field_name": "body", "value": {"value": "<p>x</p>"}})
        await rec("46-set-body-plain-text", SET, {"entity": plain, "field_name": "body", "value": {"value": "x", "format": "plain_text"}})
        await rec("47-set-moderation-state", SET, {"entity": plain, "field_name": "moderation_state", "value": {"value": "draft"}})
        await rec("48-values-after-sets", VALUES, {"entity": plain, "fields": "title"})  # the ORIGINAL handle
        await rec("49-defs-news", DEFS, {"entity_type_id": "node", "bundle": "news"})

    await probe.session(body)


async def phase_transitions(probe: Probe) -> None:
    """No saves: which moderation states field_set_value accepts on a new stub, per bundle."""
    STUB, SET, VALUES = (_tool(x) for x in ("entity_stub", "field_set_value", "entity_field_values"))

    async def body(session: ClientSession, init) -> None:
        n = 50
        for bundle in ("article", "news", "standard_page", "student_announcement"):
            token = _find_token(_text_json(await probe.call(session, STUB, {"entity_type_id": "node", "bundle": bundle, "base_fields": {"title": "Dreachy probe (unsaved)"}})))
            default = await probe.call(session, VALUES, {"entity": token, "fields": "moderation_state"})
            print(bundle, "default:", json.dumps(_text_json(default).get("data")))
            for state in ("draft", "review", "needs_review", "in_review", "ready_for_review", "archived"):
                out = await probe.call(session, SET, {"entity": token, "field_name": "moderation_state", "value": {"value": state}})
                save(f"responses/{n}-transition-{bundle}-{state}.json", out)
                n += 1

    await probe.session(body)


async def phase_write(probe: Probe) -> None:
    """ONE unpublished draft: stub -> set body -> save -> reload -> read back."""
    STUB, SET, SAVE = _tool("entity_stub"), _tool("field_set_value"), _tool("entity_save")
    VALUES = _tool("entity_field_values")
    title = f"Dreachy MCP probe {time.strftime('%Y-%m-%d %H:%M')}"
    state: dict[str, Any] = {}

    async def body(session: ClientSession, init) -> None:
        async def rec(name: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
            out = await probe.call(session, tool, args)
            save(f"responses/{name}.json", out)
            return out

        stubbed = await rec("30-stub", STUB, {"entity_type_id": "node", "bundle": "standard_page", "base_fields": {"title": title, "status": False}})
        first = _find_token(_text_json(stubbed))
        if not first:
            print("stub returned no token; stopping before any save")
            return
        # A stub is already moderation_state=draft; setting it again fails
        # (no draft->draft transition). basic_html was refused: no format.
        body_value = {"value": "Written through MCP by Dreachy's R4 probe. Safe to delete."}
        # article's "AI Pre-Moderation" workflow has no draft->draft transition, so
        # a new article can't be saved as a draft; standard_page (Editorial) can.
        # Its body is `description`; `preview_text` is also required.
        updated = await rec("31-set-body", SET, {"entity": first, "field_name": "description", "value": body_value})
        newest = _find_token(_text_json(updated)) or first
        previewed = await rec("31b-set-preview", SET, {"entity": newest, "field_name": "preview_text", "value": body_value})
        newest = _find_token(_text_json(previewed)) or newest
        state["tokens"] = [first, newest]
        await rec("32-old-handle-after-set", VALUES, {"entity": first, "fields": "description"})
        await rec("33-new-handle-after-set", VALUES, {"entity": newest, "fields": "description"})
        saved = await rec("34-save", SAVE, {"entity": newest})
        nid = _entity_metadata(_text_json(saved)).get("id")
        state["nid"] = int(nid) if nid else None
        if state["nid"]:
            await _readback(probe, session, state["nid"])

    started = time.monotonic()
    await probe.session(body)
    print(f"write session total: {time.monotonic() - started:.2f}s (draft nid {state.get('nid')}, tokens {state.get('tokens')})")


async def _readback(probe: Probe, session: ClientSession, nid: int) -> None:
    LOAD, VALUES = _tool("entity_load_by_id"), _tool("entity_field_values")
    reloaded = await probe.call(session, LOAD, {"entity_type_id": "node", "entity_id": nid})
    save("responses/35-reload.json", reloaded)
    token = _find_token(_text_json(reloaded))
    for field in ("status", "moderation_state", "title", "description", "uid", "ai_automator_status"):
        save(f"responses/36-readback-{field}.json", await probe.call(session, VALUES, {"entity": token, "fields": field}))


async def phase_readback(probe: Probe, nid: int) -> None:
    """Re-read a draft the write phase saved (no writes)."""
    async def body(session: ClientSession, init) -> None:
        await _readback(probe, session, nid)

    await probe.session(body)


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else "list"
    probe = Probe()
    if phase == "list":
        anyio.run(phase_list, probe)
    elif phase == "read":
        anyio.run(phase_read, probe)
    elif phase == "read2":
        anyio.run(phase_read2, probe)
    elif phase == "read3":
        anyio.run(phase_read3, probe)
    elif phase == "shapes":
        anyio.run(phase_shapes, probe)
    elif phase == "transitions":
        anyio.run(phase_transitions, probe)
    elif phase == "write":
        anyio.run(phase_write, probe)
    elif phase == "readback":
        anyio.run(phase_readback, probe, int(sys.argv[2]))
    else:
        sys.exit(f"phase {phase!r} not written yet")
