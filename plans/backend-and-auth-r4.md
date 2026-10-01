# R4 — McpBackend: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dreachy can talk to a Drupal site over its MCP server instead of JSON:API, through the same `Backend` interface, so every tool and the watcher work unchanged. The installer picks the backend on the settings page. Extra site tools discovered over MCP can be exposed only through an allowlist; publish, homepage and delete tools never.

**Architecture:**
- **`McpBackend(Backend)`** (`dreachy/mcp_backend.py`) implements every `Backend` method as one or more MCP tool calls.
- **One MCP session per backend method:** each method runs its whole call chain inside one session, so handle tokens never outlive the session that issued them.
- **A sync bridge** (`dreachy/mcp_session.py`) opens the session with the official `mcp` SDK, already installed as 2.0.0, in the calling worker thread. It takes the Bearer header from drupal-api-client's token handling (R2's login, with its scope), and maps failures to `DreachySiteError` and `DreachyAuthError`.
- **A mapping layer** (`dreachy/mcp_mapping.py`) turns "interface operation" into "this site's tool name and arguments". It's discovered from `tools/list` where possible, overridable per site, and keeps `check_access=TRUE` impossible to turn off.
- **Extras** go through one Dreachy proxy tool (`drupal_site_action`), allowlist-only, with a hard deny-list and the R3 `confirmed=true` rule for anything that writes.

**Tech Stack:** Python 3.12, uv, pytest, `mcp` 2.0.0 (streamable HTTP client), httpx `MockTransport` (an in-memory fake MCP site for tests), drupal-api-client 0.3.1 (OAuth token), FastAPI, plain HTML/JS.

**Spec:** `specs/backend-and-auth.md` (R4 section, Invariants, Amendments). **Research input:** `docs/mcp-findings.md`, which supersedes the spec's guesses about the server (Vincenzo, 2026-10-01).

**Branch:** `feat/r4-mcp-backend`, cut from `origin/dev` (`237b38d`, R3 merged), carrying R3's live-check commits that missed the merge (`6db637e`, `913c141`). Baseline: **195 passed**.

## Global Constraints

- **JSON:API stays the default.** Anonymous read-only JSON:API remains the zero-config default; `DREACHY_BACKEND=mcp` is opt-in. A user who sets only the site URL gets today's behaviour.
- **No forking:** never modify `reachy_mini_conversation_app`. Its pin stays at `d44fc7191e34fd293e37d45f8a84b5a986e38a58`.
- **The tools don't change:** the seven tools keep their names and speech-facing behaviour, as does the watcher.
- **Secrets** are confined to the client/auth layer, never logged, never in the profile or repo.
- **Every R3 guarantee holds in MCP mode, enforced by Dreachy rather than assumed from the server:** notes are draft-only, `confirmed=true` is required, the result is checked after saving, and reads are published-only unless a caller opts in.
- **`check_access` is always TRUE** on every search and list. Neither configuration nor the extras proxy can turn it off.
- **Never exposed:** publish, homepage and delete tools. Canvas tools are allowlist-only extras.
- **Tests:** pytest, no network; MCP is mocked through the real SDK client against a fake server. Existing tests keep passing.
- `uv` only. Documentation and comments in British English.
- **Definition of done:** tests green, README and settings page updated, a CHANGELOG entry, `docs/mcp-findings.md` pinned (module and client versions), and a live check against the DrupalForge MCP sandbox.

## Spec vs code (and findings): divergences found before planning

**Confirmed first-hand today** (anonymous requests; appended to the findings doc):
- the server is the **`mcp_server`** module, at **`/mcp`**;
- it publishes **standard OAuth discovery** (RFC 9728 and RFC 8414), with **client credentials** and its own **scopes** (`demo:mcp:connect`, `demo:content:read`, `demo:content:write`, `demo:canvas:*`);
- **JSON:API is off** on that site.

1. **R4.2 still says "the five interface methods".** `Backend` now has nine: R1 and R2's five, R3's `get_pending_nodes`, `create_draft` and `can_edit`, plus `close`. `McpBackend` implements all of them, using the findings' mapping table. *(Proposed amendment to R4.2.)*
2. **R4.2 "Reuse AuthProvider if the module's auth allows":** it does. The server takes client credentials from Simple OAuth, so R2's login applies unchanged. `DREACHY_OAUTH_SCOPE` (R2, drupal-api-client 0.3.1) carries `demo:mcp:connect demo:content:read`, plus `demo:content:write` for editing. The token comes from drupal-api-client's `ApiClient`, the same refresh margin and the same 401 retry, just not through JSON:API. One gap: forcing a refresh after a 401 *from MCP* means resetting the library's private cached token. That's ledgered, and noted as a candidate for drupal-api-client 0.3.2.
3. **R4.3 extras: upstream has an MCP mechanism, but it's the wrong one.** The conversation app's "tool spaces" register remote MCP tools, but they're built for Hugging Face Spaces (HF auth) and call the server directly, bypassing Dreachy's `confirmed=true` guard. Registering each discovered tool individually from the wrapper isn't possible without forking. So the plan exposes extras through **one Dreachy proxy tool**, `drupal_site_action`, with the allowlisted names as its `action` enum, built at start like the find tool's types. *(Proposed amendment to R4.3.)*
4. **Publish and homepage conflict with the spec.** Findings §4 allows `canvas_publish_auto_saves` and `canvas_set_homepage` with explicit installer opt-in, but the spec's **Out of scope** forbids publish, update and delete tools outright. The plan hard-denies publish, homepage and every delete tool, even if allowlisted; a deny-list wins over the allowlist. *(Ruling request.)*
5. **Handle tokens are almost certainly session-scoped.** Hence one MCP session per backend method, with the whole chain inside it. The cost is an extra `initialize` round trip per method call. Task 1 verifies whether handles outlive a session; if they do, a pooled session becomes a later optimisation, not a correctness need.
6. **`get_article` loses path lookups in MCP mode.** It takes "a URL path alias or an exact title", but MCP offers neither: no router, no title filter in the findings. So a title or path is resolved by **search**, then load, then field values. A path string becomes a semantic query, which works poorly. *(Ruling request: accept for MCP mode, and document it.)*
7. **`find_content`'s `content_type` filter depends on the search index.** `content_vector` covers articles, news and pages only. A bundle outside the index is unsearchable in MCP mode, and the filter is applied after loading each hit. Each hit also costs a load and a field-values call to build the node dict (title, type, created, summary), so a 5-result search is about 1 + 10 calls in one session. Latency is measured in Task 1.
8. **Schema discovery gets better.** `entity_field_value_definitions` returns field **types**, so text fields are picked by type (`text_with_summary`, `text_long`) rather than inferred from values (R1). R1's name preferences (`body`, then `field_body`) and teaser hints still order them. Where the bundle list comes from is a Task 1 verification.
9. **Pending is unverified server-side (findings §6).** If `entity_list` can't filter on status or moderation state, the options are:
   - (a) list the latest changes and read each item's status, about 2N calls;
   - (b) a server-side addition;
   - (c) pending is unavailable in MCP mode.

   This site has no JSON:API, so the findings' "JSON:API path" fallback doesn't apply here. *(Ruling after Task 1.)*
10. **Upstream has nothing for the JSON:API-vs-MCP demo contrast** (findings §5). It needs one site with **both** backends. The DrupalForge site has JSON:API off, and `drupal-headless` has no MCP server. *(Ruling request: enable JSON:API read-only on the DrupalForge sandbox for the contrast, or drop it from R4's live check.)*
11. **The SDK version may move.** `mcp` 2.0.0 is installed because the conversation app requires `mcp>=1.27.1`. Dreachy declares it directly (`mcp>=2.0,<3`) and pins it in the findings doc. Its streamable-HTTP client takes an injected `httpx.AsyncClient`, which is how the Bearer header and the test transport go in.
12. **R4.5 "five tools green" is now seven, plus the watcher,** all against the fake MCP site.

## Rulings (Vincenzo, 2026-10-01)

1. **Hard-deny wins over any allowlist:** publish, homepage and delete tools are never exposed.
2. **One MCP session per backend method.** Pooling only later, as an optimisation, and only if Task 1 shows handles survive across sessions.
3. **Path reads degrade to search in MCP mode,** documented in the README's MCP limits.
4. **Pending in MCP mode:** use the server's status filter if it has one. Otherwise option (a), capped at about 20 items. Option (c) only if (a) is unusable. Option (b), a server-side addition, is out of scope.
5. **Demo contrast:** Vincenzo enables JSON:API read-only on the sandbox.
6. **Credentials:** Vincenzo creates the consumer (`demo:mcp:connect demo:content:read demo:content:write`) and supplies them before Task 1 Step 2. Tasks 0, 2 and 3 start meanwhile.

Still proposed, not yet ruled: the R4.2 and R4.3 spec amendments (divergences 1 and 3).

**Correction found at Task 2 start:** `mcp` 2.0.0 is built on **`httpx2`** (2.10.0), a separate package from `httpx`. The SDK's client injection, the test `MockTransport` and the exceptions to catch all come from `httpx2`, and its `create_mcp_http_client(headers=, timeout=, auth=)` builds clients with MCP-friendly timeouts. The Task 2 code below is written against `httpx2` accordingly.

## Corrections from Task 1 (2026-10-01)

Task 1's recordings (`tests/fixtures/mcp/`, findings "Verified from Dreachy") overrule the provisional text below wherever they disagree:

1. **Envelope.** Every tool answers `{"success", "message", "data"}`, and failures are `success: false` with `isError: false`. `ToolCaller.call` returns `data` and raises `McpToolError` on `success: false`; `call_with_message` also returns the message (list totals). *Done in Task 1 Step 4.*
2. **A refused token is a 401 with an HTML body**, which the SDK reports as a generic -32603. `McpSession` watches HTTP statuses with a response hook. *Done.*
3. **Handles outlive their session and are immutable snapshots.** An old handle isn't refused; it silently lacks later changes. The chain helpers parse the handle from the server's sentence ("Entity object handle token: … Entity metadata: {json}"), `Handle` carries the metadata, and `save` returns it (the id is a digit string). `ChainMapping` loses `token_key`; `stub_args` takes `base_fields`; `values_args` takes one optional field. *Done.*
4. **Task 4 argument builders**, as served: `entity_list(entity_type_id, bundle, amount, offset, sort_field, sort_order, fields)` (`fields` takes ONE name); `search_index(index, search_words, amount, page, check_access, …)`; `entity_load_by_id(entity_type_id, entity_id: int)`; `entity_field_values(entity, fields)`; `entity_field_value_definitions(entity_type_id, bundle)`; `entity_stub(entity_type_id, bundle, base_fields)`; `field_set_value(entity, field_name, value: object)`; `entity_save(entity)`. The served names carry a `tool_api__` prefix, so suffix discovery holds. The search tool's schema has no index `enum`: the index comes from `DREACHY_MCP_SEARCH_INDEX` (sandbox: `content_vector`).
5. **Task 5.** There is no status filter; `fields: "status"` on `entity_list` returns each item's status in the same call. Field values are flat: text is a value string (HTML, no format); `created` and `changed` are unix-timestamp strings. Search hits carry `title`, `type`, `description` and `preview`, and repeat per chunk **and per language**, so dedupe by NID and keep the site language. Bundle list: see ruling request A.
6. **Task 6.** Setting `moderation_state` explicitly is wrong: a stub is already `draft`, and on a workflow without draft→draft (this sandbox's `article`) the set fails validation. See ruling request B for the replacement. Text goes in as `{value, format: plain_text}` (`basic_html` was refused). The body field is per-bundle (`description` on most bundles here), and required fields block the save (ruling request C).
7. **Task 6, pending (Ruling 4):** two single-field lists of the latest 20 by `changed` (`status`, then `moderation_state`), joined by id: pending means status `0` and not archived, as in R3. That's two calls, not 2N.
8. **Task 8.** Every served tool says `readOnlyHint: false`, reads included, so on this site every extra needs `confirmed`. That is the plan's fail-safe default, now the norm. The hard deny-list also takes `destructiveHint: true` and names containing `discard` (`canvas_discard_auto_save`). `canvas_publish_auto_saves` and `canvas_set_homepage` say `destructiveHint: false`; they're denied by name, as planned.
9. **Fake fidelity.** `tests/_fake_mcp.py` answers with the recorded names, shapes, envelope, 401 forms and handle semantics. `Raw` replays a recorded result verbatim, and the tests use it on the fixtures.

## Review Focus

1. **A write chain breaks mid-way** (stub created, a field set fails, save fails, or save succeeds but the check fails). Expected: nothing is saved before `save`, and a note that comes back published is an error. → Task 6, `test_a_chain_broken_before_save_writes_nothing` and `test_a_note_the_site_published_over_mcp_is_an_error`.
2. **`check_access` turned off** by configuration, a mapping override, or extras arguments. Expected: forced TRUE regardless. → Task 4, `test_check_access_cannot_be_overridden`; Task 8, `test_extras_cannot_switch_off_check_access`.
3. **A stale handle token is reused** after `field_set_value` returned a new one. Expected: the chain always uses the newest token. → Task 3, `test_the_chain_always_uses_the_newest_token`.
4. **Publish, homepage or delete reachable** through the allowlist. → Task 8, `test_publish_homepage_and_delete_are_never_exposed`.
5. **An MCP auth or session failure** (401, refused tool, server down). Expected: it reaches the tools as `DreachyAuthError` or `DreachySiteError`, the watcher survives, and the secret never appears in a message or the log. → Task 2, `test_a_refused_mcp_login_is_a_dreachy_auth_error` and `test_mcp_failures_never_reveal_the_secret`.

---

### Task 0: Branch, findings, plan

- [x] Confirm `git status -sb` shows `## feat/r4-mcp-backend`, then run `uv run pytest -q` and expect **195 passed**.
- [x] Commit `docs/mcp-findings.md`: Vincenzo's inspection, unchanged, plus the appended "Confirmed first-hand" section. Also commit this plan and `scripts/live/run_on_laptop.py` (R3 test infrastructure that was never committed):

```bash
git add docs/mcp-findings.md plans/backend-and-auth-r4.md scripts/live/run_on_laptop.py
git commit -m "docs: MCP findings (first-hand transport/auth facts appended), R4 plan; laptop launcher

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 1: First-hand verification spike (live; needs Ruling 6's credentials)

The findings' honest limits come first: this task exercises the server **from Dreachy's side** and records what it actually does. Later tasks build on the recordings, not on schemas read in a chat client.

**Files:**
- Create: `scripts/live/mcp_probe.py`, which records the server
- Create: `tests/fixtures/mcp/` (`initialize.json`, `tools_list.json`, `responses/*.json`), the recorded fixtures Tasks 2–8 test against
- Modify: `docs/mcp-findings.md` (a new "Verified from Dreachy" section, with pinned versions)

- [x] **Step 1: Write `scripts/live/mcp_probe.py`.** It reads the same `DREACHY_*` settings as the app (base URL, OAuth client, `DREACHY_OAUTH_SCOPE`) and gets a token through drupal-api-client. Then it runs, through the `mcp` SDK, **read-only calls only**:
   1. `initialize`, saving the protocol version, `serverInfo` (name and version, which pins the module) and capabilities;
   2. `tools/list`, saving every tool's name, input schema, output schema and annotations;
   3. `entity_list` for `node` with a bundle filter, `created` descending sort and a limit, then the same with a status filter. Record whether status filtering is accepted (findings §6);
   4. `search_index` on the content index for a known word, with `check_access` TRUE. Record the hit shape (`entity:node/NID:LANG:CHUNK`, duplicates per chunk, score);
   5. `entity_load_by_id`, then `entity_field_values` on the returned token. Record the token format, the field-value shapes (formatted text: `value`/`format`/`processed`?) and timestamp formats;
   6. reuse of a handle token in a **second** session. Record whether it still works;
   7. `entity_field_value_definitions` for each node bundle, and where the bundle list comes from;
   8. error shapes: an unknown tool; an admin-only tool (`system_status`); a malformed argument; an expired or garbage token (expect 401);
   9. timing: the wall-clock cost of `initialize` and of a 3-call chain.

   Each response is saved to `tests/fixtures/mcp/responses/<step>.json`, with tokens and secrets redacted.
- [x] **Step 2: Run it** against the DrupalForge sandbox. Record **write** behaviour separately, only once Ruling 6's consumer has `demo:content:write`: `entity_stub`, then `field_set_value` (title, body, `moderation_state=draft` on a moderated bundle), then `entity_save`. Re-load the result and record `status` and `moderation_state`. One unpublished draft is created, titled "Dreachy MCP probe <time>".
- [x] **Step 3: Pin the facts** in `docs/mcp-findings.md` under "Verified from Dreachy (date)": the `mcp_server` version, the protocol version, `mcp` 2.0.0, the answers to every §6 question, and the measured latency.
- [x] **Step 4: Rule.** Correct Task 4's provisional argument builders to the recorded schemas; each correction gets a `Ruling:` line in the progress file. Decide Ruling 4 (pending) with Vincenzo.
- [x] **Step 5: Commit** the probe, the fixtures and the doc.

*Tasks 2–3 don't depend on Task 1 and can start in parallel; Tasks 4–8 start from its fixtures.*

---

### Task 2: The MCP session bridge (sync, authenticated, errors mapped)

**Files:**
- Create: `dreachy/mcp_session.py`
- Create: `tests/_fake_mcp.py` (an in-memory MCP server behind `httpx.MockTransport`, speaking JSON-RPC over streamable HTTP: `initialize`, `notifications/initialized`, `tools/list`, `tools/call`)
- Test: `tests/test_mcp_session.py`

**Interfaces:**
- Produces:
  - `McpSession(config, *, http_client_factory=None)`
  - `.run(chain: Callable[[ToolCaller], Awaitable[T]]) -> T`: opens one session, initializes, awaits `chain(caller)` and closes. It runs in the calling thread with `anyio.run`; every caller is already a worker thread (tools use `asyncio.to_thread`, settings routes the threadpool).
  - `ToolCaller.call(name, arguments) -> dict`: returns the result's `structured_content`, or the first text content parsed as JSON. A result with `is_error` raises `McpToolError(name, message)`, a `DreachySiteError`.
  - `.list_tools() -> list[mcp.types.Tool]`
  - Errors: HTTP 401 or 403, or JSON-RPC `-32001`, become `DreachyAuthError` (after one forced token refresh). Transport failures become `DreachySiteError`. No secret appears in any message.

- [ ] **Step 1: The fake server.** `tests/_fake_mcp.py` defines `FakeMcpSite(tools: dict[str, Callable[[dict, SessionState], dict]], *, require_token: str | None, annotations: dict | None)`. Its `handle(request)` checks `Authorization: Bearer <token>` (or answers 401 with the realm header) and parses the JSON-RPC body:
   - `initialize` answers `{"protocolVersion": "2026-07-28", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake mcp_server", "version": "0"}}` and sets an `Mcp-Session-Id` header;
   - notifications get `202`;
   - `tools/list` lists the tools, with input schemas and annotations;
   - `tools/call` runs the Python tool against per-session state (handle tokens live in `SessionState`, so they're session-scoped by construction).

   Responses are `application/json`. It also offers `.calls` (a log of `(session_id, tool, arguments)`) and `.client(...)`, which returns an `httpx.AsyncClient` on the transport.
- [ ] **Step 2: Failing tests** (`tests/test_mcp_session.py`). Each runs the real `mcp` SDK client against the fake:
   - `test_a_chain_runs_in_one_session`: three calls, one `Mcp-Session-Id`.
   - `test_each_run_is_a_new_session`
   - `test_the_bearer_token_comes_from_the_site_login`: the fake requires the token the `PrivateSite`-style token endpoint grants.
   - `test_a_refused_mcp_login_is_a_dreachy_auth_error`: a 401 triggers one token refresh and one retry, then raises.
   - `test_a_tool_error_is_a_site_error_with_the_servers_message`
   - `test_a_server_that_isnt_there_is_a_site_error`
   - `test_mcp_failures_never_reveal_the_secret` (checks `caplog` and the exception text)
- [ ] **Step 3: Implement `dreachy/mcp_session.py`:**

```python
"""One authenticated MCP session per call chain, from synchronous code.

Backend methods are synchronous (tools call them via asyncio.to_thread), the
mcp SDK is async: run() opens a session in the calling worker thread with
anyio.run, so nothing ever blocks the conversation app's event loop. One
session per run(): the server's entity handle tokens belong to the session
that issued them (docs/mcp-findings.md), so a whole chain runs inside one.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import anyio
import httpx
from drupal_api_client import ApiClient, AuthenticationError
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .auth import library_authentication
from .backend import DreachyAuthError, DreachySiteError
from .config import Config

T = TypeVar("T")
_REFUSED = "the site refused Dreachy's MCP login — check the site login on Dreachy's settings page"


class McpToolError(DreachySiteError):
    """A tool call the server answered with is_error."""


class ToolCaller:
    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._session.call_tool(name, arguments)
        if result.is_error:
            text = " ".join(getattr(c, "text", "") for c in result.content)[:200]
            raise McpToolError(f"{name}: {text or 'tool error'}")
        if result.structured_content is not None:
            return dict(result.structured_content)
        for content in result.content:
            if getattr(content, "type", "") == "text":
                try:
                    return json.loads(content.text)
                except ValueError:
                    return {"text": content.text}
        return {}


class McpSession:
    def __init__(self, config: Config, *, http_client_factory: Callable[[dict[str, str]], httpx.AsyncClient] | None = None) -> None:
        self.config = config
        # Token handling is drupal-api-client's (R2): grant, cache, refresh margin.
        self._auth = ApiClient(config.base_url, authentication=library_authentication(config))
        self._factory = http_client_factory or (lambda headers: httpx.AsyncClient(headers=headers, timeout=config.request_timeout))

    def _headers(self) -> dict[str, str]:
        try:
            return self._auth.add_authorization_header()
        except AuthenticationError as exc:
            raise DreachyAuthError(_REFUSED) from exc

    def run(self, chain: Callable[[ToolCaller], Awaitable[T]]) -> T:
        try:
            return anyio.run(self._run, chain)
        except _Unauthorised:
            # One retry with a fresh token (the library's 401 retry covers its
            # own HTTP calls, not MCP's): drop the cached token and go again.
            self._auth._oauth_token_response = None  # noqa: SLF001 — see plan divergence 2
            try:
                return anyio.run(self._run, chain)
            except _Unauthorised as exc:
                raise DreachyAuthError(_REFUSED, status=401) from exc

    async def _run(self, chain: Callable[[ToolCaller], Awaitable[T]]) -> T:
        try:
            async with self._factory(self._headers()) as http:
                async with streamable_http_client(self.config.mcp_endpoint, http_client=http) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        return await chain(ToolCaller(session))
        except DreachySiteError:
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise _Unauthorised() from exc
            raise DreachySiteError(f"the MCP server answered {exc.response.status_code}", status=exc.response.status_code) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise DreachySiteError(f"the MCP server can't be reached ({type(exc).__name__})") from exc


class _Unauthorised(Exception):
    pass
```

   `Config.mcp_endpoint` arrives in Task 7. Until then the tests construct `Config` and set it directly. **Ruling at implementation:** how the SDK surfaces a 401 (an `HTTPStatusError`, an `ExceptionGroup` from the task group, or a JSON-RPC error) is pinned by `test_a_refused_mcp_login_is_a_dreachy_auth_error`; unwrap `ExceptionGroup` if that's what arrives.
- [ ] **Step 4: Run** `uv run pytest tests/test_mcp_session.py -q` (7 pass), then the full suite.
- [ ] **Step 5: Commit** `feat: authenticated MCP session bridge (one session per call chain)`, adding `mcp>=2.0,<3` to `pyproject.toml` dependencies, then `uv lock`.

---

### Task 3: Call chains with handle-token passing

**Files:**
- Create: `dreachy/mcp_chain.py`
- Test: `tests/test_mcp_chain.py`

**Interfaces:**
- Produces:
  - `Handle(token: str)`
  - `async load(caller, mapping, entity_type, entity_id) -> Handle`
  - `async field_values(caller, mapping, handle) -> dict`
  - `async stub(caller, mapping, entity_type, bundle) -> Handle`
  - `async set_value(caller, mapping, handle, field, value) -> Handle`: returns the **new** handle.
  - `async save(caller, mapping, handle) -> dict` (`{type, bundle, id, revision}`)
  - Each helper reads tool names and argument shapes from the mapping (Task 4). Until Task 4, the tests pass a `McpMapping` built by hand.

- [ ] **Failing tests:**
   - `test_the_chain_always_uses_the_newest_token`: the fake's `field_set_value` invalidates the previous token, so passing an old token errors.
   - `test_load_then_field_values_in_one_session`
   - `test_a_stub_is_never_saved_unless_save_is_called`: the fake records saves.
- [ ] **Implement** thin async helpers. `set_value` returns `Handle(response[mapping.token_key])`, and callers must rebind (`handle = await set_value(...)`). A docstring states the newest-token rule.
- [ ] **Commit:** `feat: MCP call chains with handle-token passing`.

---

### Task 4: The site-configurable mapping layer

**Files:**
- Create: `dreachy/mcp_mapping.py`
- Test: `tests/test_mcp_mapping.py` (against `tests/fixtures/mcp/tools_list.json` from Task 1)

**Interfaces:**
- Produces `McpMapping`, a frozen dataclass with:
  - tool names: `search`, `entity_list`, `load`, `field_values`, `field_definitions`, `metadata`, `stub`, `set_value`, `save`;
  - `search_index`, `token_key`;
  - argument builders: `search_args(query, *, limit)`, `list_args(entity_type, *, bundle, limit, sort, status)`, `load_args(entity_type, entity_id)`, `values_args(token)`, `definitions_args(entity_type, bundle)`, `stub_args(entity_type, bundle)`, `set_args(token, field, value)` and `save_args(token)`;
  - `discover_mapping(tools: list[mcp.types.Tool], overrides: dict) -> McpMapping`.

- [ ] **Discovery:** match each operation by **suffix** in the site's tool names: `*search_index`, `*entity_list`, `*entity_load_by_id`, `*entity_field_values`, `*entity_field_value_definitions`, `*entity_metadata`, `*entity_stub`, `*field_set_value`, `*entity_save`. The `demo_` prefix is this site's choice. Take the search index from the search tool's input-schema `enum` if one exists; otherwise from `DREACHY_MCP_SEARCH_INDEX`. **Overrides** come from `DREACHY_MCP_MAPPING`, a JSON object of `operation: tool_name`, and win over discovery. A missing required operation raises `DreachySiteError("this site's MCP server lacks <operation>")`.
- [ ] **Argument builders.** Provisional, from the findings' vocabulary; Task 1 Step 4 corrects them to the recorded schemas. `search_args` always sets `check_access: True` **after** applying any extra parameters, so nothing can switch it off.
- [ ] **Failing tests:**
   - `test_discovery_maps_this_sites_tools` (the fixture);
   - `test_a_differently_prefixed_site_is_mapped_too` (a renamed copy of the fixture);
   - `test_overrides_win_over_discovery`;
   - `test_a_missing_operation_is_a_clear_site_error`;
   - `test_check_access_cannot_be_overridden` (an override and extra parameters both try `check_access: False`).
- [ ] **Commit:** `feat: site-configurable MCP tool and index mapping`.

---

### Task 5: `McpBackend` reads

**Files:**
- Create: `dreachy/mcp_backend.py`
- Test: `tests/test_mcp_backend.py` (`FakeMcpSite` seeded from the Task 1 fixtures)

**Interfaces:**
- Consumes: `McpSession` (Task 2), the chain helpers (Task 3), `McpMapping` (Task 4)
- Produces: `McpBackend(config, *, http_client_factory=None, auto_discover=False)`, implementing:
  - `get_schema()`
  - `get_recent_nodes(limit, *, include_unpublished)`
  - `find_content(keyword, content_type, *, include_unpublished)`
  - `get_article(title_or_path, *, include_unpublished)`
  - `close()`
  - `get_site_pulse` is inherited (composed from recent nodes, never `system_status`)

**Behaviour, one session per method:**
- **`get_schema`:** the bundle list (source per Task 1), then `field_definitions` per bundle. Text fields are picked **by type** (`text_with_summary`, `text_long`, `text`), ordered by R1's preferences (`body`, `field_body`, then the rest), with the teaser by name hint as before. A bundle with no text field is skipped. `moderated` comes from a `moderation_state` field being present.
- **`get_recent_nodes`:** `entity_list` per enabled type, `created` descending, published-only through the status filter (per Task 1; otherwise filtered after the load). Each item is turned into a node dict via `field_values` in the same session.
- **`find_content`:** `search` on the index. Hits are **deduped by NID** (the first chunk wins), then loaded and given field values. A `content_type` filter is applied after loading, and an unknown or disabled type widens to all enabled types (R1 rule). Unpublished hits are dropped unless `include_unpublished`.
- **`get_article`:** `search`, then load the top hit, then field values. Path strings are searched as text (divergence 6). Published-only unless `include_unpublished`.
- **Node dicts** keep the `Backend` shape exactly (`id`, `title`, `type`, `created`, `changed`, `path`, `body`, `summary`, `status`, `moderation_state`). Timestamps are normalised to ISO 8601 (the format per Task 1), and `body` and `summary` go through R1's `_strip_html`.

**Failing tests** (key names): `test_find_dedupes_chunks_of_the_same_node`, `test_find_never_sends_check_access_false`, `test_get_article_resolves_through_search_load_values_in_one_session`, `test_recent_is_published_only`, `test_schema_picks_text_fields_by_type`, plus `test_*_failure_is_a_site_error` for each method.

- [ ] **Commit:** `feat: McpBackend reads over the site's MCP tools`.

---

### Task 6: `McpBackend` editorial: pending, `create_draft`, `can_edit`

- **`create_draft(content_type, title, body)`** runs one session:
  1. `stub`;
  2. set the title, giving a new handle;
  3. set the body (`{value, format: plain_text}`), giving a new handle;
  4. `moderation_state=draft` on moderated types, otherwise `status=false`, giving a new handle;
  5. `save`;
  6. **reload** by the saved id and read `status`.

  A published result raises the R3 error. A failure before `save` writes nothing: unsaved stubs vanish with the session. The R3 refusals hold: no write before discovery, and only enabled types.
- **`get_pending_nodes`** per Ruling 4.
- **`can_edit`:** logged in, a token granted, **and** the mapping has `stub`, `set_value` and `save`.
- **Failing tests:**
  - `test_a_note_over_mcp_is_draft_only_and_verified_after_save`
  - `test_a_chain_broken_before_save_writes_nothing`
  - `test_a_note_the_site_published_over_mcp_is_an_error`
  - `test_no_mcp_write_before_discovery`
  - `test_can_edit_needs_the_write_tools`
- [ ] **Commit:** `feat: McpBackend drafts, pending and the login check`.

---

### Task 7: Choosing the backend (config, factory, settings page)

- **`Config`** gains:
  - `backend: str = "jsonapi"` (`DREACHY_BACKEND=jsonapi|mcp`; anything else means JSON:API, with a warning);
  - `mcp_endpoint: str` (`DREACHY_MCP_ENDPOINT`; default `{base_url}/mcp`);
  - `mcp_mapping: dict` (`DREACHY_MCP_MAPPING`, JSON);
  - `mcp_search_index: str`;
  - `mcp_extra_tools: tuple[str, ...]` (`DREACHY_MCP_EXTRA_TOOLS`, comma-separated).
- **`tools/_shared.get_client`** builds `McpBackend` when `backend == "mcp"`, otherwise `JsonApiBackend`. That's the factory R2 deferred `DREACHY_BACKEND` to.
- **Settings:** a new `GET`/`POST /api/backend` (R1 to R3's exact-dict tests pin the existing routes) for `{backend, mcp_endpoint, mcp_extra_tools}`, written through `_set_env`, with `reset_client()` on change. The Advanced section gains a backend select, the endpoint, and an extras text field. `/api/status` reports which backend is in use.
- **Failing tests:**
  - `test_jsonapi_stays_the_default`
  - `test_mcp_backend_is_chosen_by_setting`
  - `test_an_unknown_backend_falls_back_to_jsonapi`
  - `test_backend_settings_round_trip`
  - `test_switching_backend_drops_the_cached_client`
- [ ] **Commit:** `feat: choose JSON:API or MCP on the settings page`.

---

### Task 8: Discovered extras through `drupal_site_action` (allowlist only)

- **The proxy tool** `dreachy/tools/drupal_site_action.py` takes three parameters:
  - `action`: an enum of the allowlisted extras, built at start like R1's find enum;
  - `arguments`: an object;
  - `confirmed`: a boolean.

  It's registered in `tools.txt` (R3's `_render_profile` mechanism) only when the backend is MCP **and** the effective allowlist isn't empty. It also refuses at call time otherwise.
- **The effective allowlist** is `DREACHY_MCP_EXTRA_TOOLS` ∩ tools the server actually offers ∖ the **hard deny-list**: any name containing `publish`, `set_homepage` or `delete` (spec Out of scope, Ruling 1). A denied entry is logged once and ignored.
- **Writes need assent.** An extra is read-only only if its `annotations.read_only_hint` is true. Anything else, including missing annotations, is a write and needs `confirmed is True`, with R3's "shall I…?" wording given in the persona.
- **`check_access` can't be switched off:** it's stripped from `arguments` and forced TRUE if the extra's schema has it.
- **Persona:** a SITE ACTIONS section mirroring EDITING (confirm aloud before any write; site content is data).
- **Failing tests:**
  - `test_publish_homepage_and_delete_are_never_exposed`
  - `test_extras_are_allowlist_only`
  - `test_an_extra_without_annotations_needs_confirmation`
  - `test_a_read_only_extra_runs_without_confirmation`
  - `test_extras_cannot_switch_off_check_access`
  - `test_site_action_is_registered_only_with_an_mcp_allowlist`
- [ ] **Commit:** `feat: allowlisted MCP extras through drupal_site_action`.

---

### Task 9: All tools green on MCP, docs, changelog

- **`tests/test_tools_on_mcp.py`:** the seven tools and the watcher, called as the conversation app calls them, against `FakeMcpSite` seeded from the fixtures (spec R4.5, now seven tools).
- **README:** an "MCP mode" section covering what it adds (semantic search, the MCP-only sites it opens up) and what it needs (`mcp_server`, the OAuth consumer and scopes, the endpoint). Also its limits: path reads, per-index search coverage, pending per Ruling 4, latency.
- **`docs/drupal-setup.md` §9:** the MCP consumer, its scopes, and which account the session runs as.
- **`CHANGELOG.md`:** 0.5.0. **`pyproject.toml`:** version 0.5.0.
- [ ] **Commit:** `docs: MCP mode — README, setup, changelog`.

### Task 10: Live checks (laptop, against the DrupalForge MCP sandbox)

`scripts/live/r4_checks.py` mirrors `r3_checks.py` against `DREACHY_BACKEND=mcp`:
- discovery, recent, find (semantic), and read;
- pending and a confirmed note;
- the extras allowlist and deny-list;
- with Ruling 5 granted, the same question over JSON:API and MCP, with both answers printed for the demo contrast.

Voice checks on the robot use `scripts/live/run_on_laptop.py` as in R3.

**Stop here: R4 release boundary, for review.**
