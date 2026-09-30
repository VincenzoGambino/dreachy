# R2 — Backend seam + site login: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `tool_queries.py`, the tools, the watcher and the settings page query a site through a `Backend` interface instead of a JSON:API client, and Dreachy can optionally log in to Drupal with OAuth2 client credentials. With only a site URL set, behaviour is unchanged.

**Architecture:**
- **The seam.** A new `dreachy/backend.py` holds the `Backend` ABC. Its methods are the ones Dreachy actually calls today: `get_recent_nodes`, `find_content`, `get_article`, `get_site_pulse`, `get_schema`, plus R1's schema cache (`schema`, `full_schema`, `schema_discovered`, `refresh_schema`). That cache is the same for every backend, so it lives in the base class.
- **The implementation.** `DrupalClient` becomes `JsonApiBackend(Backend)`, and `DrupalClient` stays as an alias.
- **Login.** drupal-api-client does all the token handling. Dreachy's side is a small factory (`dreachy/auth.py`) that turns the installer's settings into the library's `OAuthAuth`, plus error mapping, so a refused login reads as "the site refused Dreachy's credentials" rather than crashing a tool or the watcher.
- **Dependency order.** Only the last task depends on drupal-api-client 0.3.1, which is being released in parallel.

**Tech Stack:** Python 3.12, uv, pytest, httpx `MockTransport`, drupal-api-client (0.3.0 for Tasks 1–5; ≥0.3.1 from Task 6), FastAPI, plain HTML/JS.

**Spec:** `specs/backend-and-auth.md` (R2 section, Invariants, Amendments)

**Branch:** `feat/r2-backend-seam-auth`, cut from `dev` (`c1d616f`, R1 merged).

## Global Constraints

- Anonymous read-only JSON:API mode remains the zero-config default. A user who sets only the site URL gets today's behaviour.
- Never fork or modify `reachy_mini_conversation_app`. The pin stays at `d44fc7191e34fd293e37d45f8a84b5a986e38a58`.
- The five tools keep their names and speech-facing behaviour.
- Secrets are confined to the client/auth layer, never logged, never in the profile or repo. They're stored in the instance-path `.env`, and `client_secret`-style fields are write-only password inputs.
- New spoken confirmations get a line in the profile instructions.
- Tests: pytest, no network (HTTP mocked). Existing tests keep passing. Baseline on `dev`: **97 passed**.
- Tasks 1–5 must run on drupal-api-client **0.3.0**. Only Task 6 pins `>=0.3.1`.
- `uv` only. Documentation and comments in British English.
- Definition of done: tests green, README and settings page updated, a CHANGELOG entry, and `docs/drupal-setup.md` verified by a clean walkthrough on the sandbox.

## Spec vs code: divergences found before planning

1. **R2.1's method names are invented.** The spec says to extract them from what the code calls, and the R1 review listed them:
   - `tool_queries.py` calls `get_recent_nodes(limit)`, `find_content(keyword, content_type=None)`, `get_article(title_or_path)` and `get_site_pulse()`.
   - The watcher calls `get_recent_nodes(limit=1)` and reads `config`.
   - The settings page uses `schema`, `full_schema`, `schema_discovered` and `refresh_schema()`.
   - `get_article` takes a path alias or an exact title, never a type and id, so the spec's `get_node(type, id_or_uuid)` has no caller.
   - `site_stats(types)` would be `get_site_pulse()`. The enabled types come from the backend's own config (`DREACHY_TYPES`), not from a parameter.
   - This plan uses the real names. *(Proposed amendment to R2.1.)*
2. **R2.3 doesn't fit the amended secret invariant.** "Backends request headers from the provider; they never see the secret" conflicts with how drupal-api-client works: it takes the `OAuthAuth` credentials and handles tokens itself (0.3.1 adds the 60 s refresh margin and the single 401 retry). So Dreachy's "AuthProvider" is a factory choosing between `none` and `oauth`, not a token cache. *(Proposed amendment to R2.3: "Dreachy selects the provider; drupal-api-client handles the token".)*
3. **`AuthenticationError` escapes Dreachy's error handling.** A failed token grant raises the library's `AuthenticationError`, which isn't an `httpx` error. Today it would reach the tools as "Something went wrong" and end the watcher's task, which only catches `DreachySiteError`. Task 6 maps it.
4. **Logging in would make Dreachy read drafts aloud.** JSON:API returns every node the account can view. A `dreachy` role that can see unpublished content, which R3's pending-content tool needs, would put drafts into what's-new, search, reading aloud and the watcher. Task 2 makes every read published-only (`filter[status]=1`) unless the caller explicitly passes `include_unpublished=True`, which no tool does. That's a no-op for anonymous access, which only sees published nodes anyway. *(Now in the spec: R2.1.)*
5. **The spec's live check needs a concrete setup.** "A permissioned-content read that anonymous cannot see" isn't possible with the published-only filter plus core permissions. Core has no per-type view permission, and drafts are now filtered out. The realistic R2 setup is a **private site**: anonymous without `access content`, and the `dreachy` role with it. Dreachy then works only when logged in. `docs/drupal-setup.md` describes it (Task 5).
6. **`DREACHY_BACKEND=jsonapi|mcp` would be a selector with one option until R4.** R2.4 asks for it now. This plan builds the seam, so R4 only adds a second `Backend`, but leaves the variable out until R4 has something to select. *(Ruling request.)*
7. **The settings routes need a separate endpoint again.** `tests/test_settings_routes.py` asserts the exact `GET /api/config` dict, as in R1. Login settings get their own `GET`/`POST /api/auth`.
8. **The instance `.env` is created world-readable** (the default umask). Once it holds a client secret, it should be `0600`, and it already holds `HF_TOKEN`. Task 4 tightens it.
9. **`Config` would print the secret.** It's a dataclass, and its default `repr` includes every field. Task 3 marks the secret `repr=False`. Separately, the library's `OAuthAuth` is a frozen dataclass whose `repr` also includes the secret, so Dreachy must never log the `authentication` object. Task 6 has a test that the secret appears in no log output and no error message.
10. **The router logs in separately.** `JsonApiClient` hands the same credentials to its `DecoupledRouterClient`, which caches its own token, so a path lookup triggers a second token grant. That's harmless; Task 6's tests account for it.
11. **One R1 test has to change.** R1's retry test patches `dreachy.client.time`. The retry logic moves to `backend.py` in Task 1, so the patch target moves with it: a two-line test change, no behaviour change.

## Rulings (Vincenzo, 2026-09-30)

- **Spec amended:** R2.1 (the real method names, and `include_unpublished`), R2.3 (Dreachy selects the provider, the library handles the token), R2.4 (`DREACHY_BACKEND` deferred to R4, which now mentions it).
- **Per-user login is R6**, not R5; Paragraphs keeps R5. The R6 section and its design decisions are in the spec.
- **Task 2:** the published-only filter is an explicit `include_unpublished=False` parameter on the backend's read methods. No tool sets it; R3's editorial tools will, deliberately.
- **drupal-api-client 0.3.1:** the contract below is confirmed as written, with `token_refresh_margin` defaulting to 60 s.
- **Execution:** Task 0, then Tasks 1–5. Stop before Task 6 if 0.3.1 isn't on PyPI yet.

### drupal-api-client 0.3.1 contract (confirmed by Vincenzo)

- `JsonApiClient(..., authentication=OAuthAuth(client_id=..., client_secret=...), token_refresh_margin=<seconds>)`, default 60, with the margin passed on to its router client. In 0.3.0 the margin is hard-coded at 10 s.
- **One retry on 401:** when an authenticated request gets a 401, the client drops its cached token, fetches a new one and retries once. A second 401 is returned as normal, so `raise_for_status=True` raises `httpx.HTTPStatusError` with status 401.
- **Failed grant:** as in 0.3.0, a failed token grant raises `drupal_api_client.AuthenticationError`, and the message contains no credentials.

## Review Focus

1. **A logged-in Dreachy reads drafts aloud.** If the `dreachy` role can view unpublished nodes, what's-new, search, read and the watcher must still see published content only. → Task 2, `test_logged_in_reads_skip_unpublished_content` and the tests beside it.
2. **The secret leaks.** Places it could show up: `repr(Config)`, `GET /api/auth`, the `POST /api/auth` response, error messages, log output, and the `.env` file's permissions. → Task 3, `test_config_repr_never_shows_the_client_secret`; Task 4, `test_get_auth_never_returns_the_secret` and `test_saving_a_secret_makes_the_env_owner_only`; Task 6, `test_a_refused_login_never_reveals_the_secret`.
3. **OAuth is selected but incomplete** (the secret was never entered, or has been cleared). Expected: anonymous access, a warning in the log, and the settings page says so; not a crash, and not a login attempt with empty credentials. → Task 3, `test_oauth_without_a_secret_falls_back_to_anonymous`; Task 4, `test_get_auth_reports_an_incomplete_login`.
4. **The site revokes Dreachy's credentials mid-session.** Tools say the site refused Dreachy's credentials, not "Something went wrong", and the watcher keeps running and backs off. → Task 6, `test_a_refused_login_reaches_the_tools_as_a_site_error` and `test_the_watcher_survives_a_refused_login`.
5. **Saving the settings page with the secret field blank.** The saved secret must be kept, not wiped, because the field is never pre-filled. → Task 4, `test_saving_with_a_blank_secret_keeps_the_saved_one`.

---

### Task 0: Branch, spec amendment, plan

- [ ] **Step 1: Confirm the branch and baseline**

Run: `git -C dreachy-app status -sb && cd dreachy-app && uv run pytest -q`
Expected: `## feat/r2-backend-seam-auth`, then `97 passed`

- [ ] **Step 2: Commit the secret-invariant amendment and this plan**

The amendment is already in `specs/backend-and-auth.md` (Invariants plus an Amendments entry).

```bash
git -C dreachy-app add specs/backend-and-auth.md plans/backend-and-auth-r2.md
git -C dreachy-app commit -m "docs: R2 plan; amend the secret-handling invariant

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 1: The `Backend` seam

**Files:**
- Create: `dreachy/backend.py`
- Modify: `dreachy/client.py` (class header, `__init__`, remove the moved members, imports, alias)
- Modify: `dreachy/tool_queries.py`, `dreachy/tools/_shared.py`, `dreachy/tools/drupal_watch_site.py` (type hints and docstrings: `DrupalClient` → `Backend`)
- Modify: `tests/test_discovery.py` (2 lines: patch target)
- Create: `tests/_fake_backend.py`
- Test: `tests/test_tools_on_backend.py`

**Interfaces:**
- Produces:
  - `dreachy.backend.DreachySiteError(message, *, status=None)` (moved; still importable from `dreachy.client`)
  - `dreachy.backend.Backend(config, *, auto_discover=False)` (ABC). Abstract: `close()`, `get_schema()`, `get_recent_nodes(limit=None)`, `find_content(keyword, content_type=None)`, `get_article(title_or_path)`. Concrete: `config`, `schema`, `full_schema`, `schema_discovered`, `refresh_schema()`, `_types()`, `get_site_pulse()`, context manager.
  - `dreachy.client.JsonApiBackend(config, *, http_client=None, auto_discover=False)`, with `DrupalClient = JsonApiBackend`
  - `tests/_fake_backend.py`: `fake_node(uuid, title, type, created, *, body="", summary="") -> dict` and `FakeBackend(nodes, config=None, *, fail=False)`
  - Node dicts (unchanged from R1): `{"id", "title", "type", "created", "changed", "path", "body", "summary"}`

- [ ] **Step 1: Write the in-memory backend and the failing tests**

`tests/_fake_backend.py`:

```python
"""An in-memory Backend: the five tools and the watcher run against it with
no HTTP at all, which is what proves they depend on the interface and not
on JSON:API."""

from __future__ import annotations

from typing import Any

from dreachy.backend import Backend, DreachySiteError
from dreachy.config import Config
from dreachy.schema import Schema


def fake_node(uuid: str, title: str, type: str, created: str, *, body: str = "", summary: str = "") -> dict[str, Any]:
    return {
        "id": uuid,
        "title": title,
        "type": type,
        "created": created,
        "changed": created,
        "path": f"/{type}/{uuid}",
        "body": body,
        "summary": summary or body[:200],
    }


class FakeBackend(Backend):
    def __init__(self, nodes: list[dict[str, Any]], config: Config | None = None, *, fail: bool = False) -> None:
        super().__init__(config or Config())
        self.nodes = nodes
        self.fail = fail
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def _check(self) -> None:
        if self.fail:
            raise DreachySiteError("site down")

    def get_schema(self) -> Schema:
        self._check()
        return self.full_schema

    def get_recent_nodes(self, limit: int | None = None) -> list[dict[str, Any]]:
        self._check()
        limit = limit or self.config.whats_new_limit
        return sorted(self.nodes, key=lambda n: n["created"], reverse=True)[:limit]

    def find_content(self, keyword: str, content_type: str | None = None) -> list[dict[str, Any]]:
        self._check()
        return [
            n
            for n in self.get_recent_nodes(limit=len(self.nodes) or 1)
            if keyword.lower() in n["title"].lower() and content_type in (None, n["type"])
        ]

    def get_article(self, title_or_path: str) -> dict[str, Any] | None:
        self._check()
        return next((n for n in self.nodes if title_or_path in (n["title"], n["path"])), None)
```

`tests/test_tools_on_backend.py`:

```python
"""The five tools, called as the conversation app calls them, against an
in-memory Backend (spec R2 check: "all five tools green against mocked
backend")."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from _fake_backend import FakeBackend, fake_node

import dreachy.tools._shared as shared
from dreachy.tools import drupal_watch_site as watch_module
from dreachy.tools.drupal_find_content import DrupalFindContent
from dreachy.tools.drupal_read_article import DrupalReadArticle
from dreachy.tools.drupal_site_pulse import DrupalSitePulse
from dreachy.tools.drupal_whats_new import DrupalWhatsNew
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


NODES = [
    fake_node("n1", "Library reopens", "news_item", _iso(9), body="The library is open again."),
    fake_node("n2", "Council approves new park", "news_item", _iso(1), body="The council voted.", summary="Park news."),
    fake_node("e1", "Harvest fair", "event", _iso(3), body="Stalls and music."),
]


@pytest.fixture(autouse=True)
def _fake_site_backend():
    shared._client = FakeBackend(list(NODES))
    watch_module._watch_task = None
    yield
    if watch_module._watch_task is not None and not watch_module._watch_task.done():
        watch_module._watch_task.cancel()
    watch_module._watch_task = None
    shared._client = None


def _call(tool, deps=None, **kwargs):
    return asyncio.run(tool(deps or ToolDependencies(reachy_mini=None, movement_manager=None), **kwargs))


def test_whats_new_runs_on_any_backend() -> None:
    result = _call(DrupalWhatsNew())

    assert [(i["title"], i["type"], i["age"]) for i in result["items"]] == [
        ("Council approves new park", "news_item", "yesterday"),
        ("Harvest fair", "event", "3 days ago"),
        ("Library reopens", "news_item", "1 week ago"),
    ]


def test_find_content_runs_on_any_backend() -> None:
    result = _call(DrupalFindContent(), keyword="park")

    assert result == {"matches": [{"title": "Council approves new park", "type": "news_item", "teaser": "Park news."}]}


def test_site_pulse_runs_on_any_backend() -> None:
    assert _call(DrupalSitePulse()) == {"node_count": 3, "latest_activity_age": "yesterday"}


def test_read_article_runs_on_any_backend() -> None:
    assert _call(DrupalReadArticle(), title_or_path="Harvest fair") == {"title": "Harvest fair", "text": "Stalls and music."}


def test_read_article_reports_nothing_found() -> None:
    assert _call(DrupalReadArticle(), title_or_path="Nope") == {"error": "I couldn't find anything matching 'Nope'."}


def test_a_backend_failure_reaches_the_tools_as_a_site_error() -> None:
    shared._client = FakeBackend([], fail=True)

    assert _call(DrupalWhatsNew()) == {"error": "I can't reach the site right now: site down"}


def test_watcher_starts_and_stops_on_any_backend() -> None:
    tool = watch_module.DrupalWatchSite()
    deps = ToolDependencies(reachy_mini=None, movement_manager=None)

    async def run():
        return await tool(deps, action="start"), await tool(deps, action="stop")

    started, stopped = asyncio.run(run())
    assert (started["status"], stopped["status"]) == ("watching", "stopped watching")


def test_watcher_reacts_to_new_content_on_any_backend(monkeypatch) -> None:
    backend = FakeBackend(list(NODES), config=None)
    backend.config.poll_interval_seconds = 0.01
    shared._client = backend
    played: list[str] = []

    async def fake_play_reaction(reaction, deps):
        played.append(reaction.name)

    monkeypatch.setattr(watch_module, "play_reaction", fake_play_reaction)

    class _Media:
        def play_sound(self, name: str) -> None:
            pass

    class _Robot:
        media = _Media()

    deps = ToolDependencies(reachy_mini=_Robot(), movement_manager=None)
    tool = watch_module.DrupalWatchSite()

    async def run():
        await tool(deps, action="start")
        await asyncio.sleep(0.1)  # baseline
        backend.nodes.append(fake_node("n3", "Brand new", "news_item", _iso(0)))
        await asyncio.sleep(0.2)
        await tool(deps, action="stop")

    asyncio.run(run())
    assert played == ["perk_up"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_tools_on_backend.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'dreachy.backend'`

- [ ] **Step 3: Create `dreachy/backend.py`**

```python
"""Backend — the interface Dreachy's tools, watcher and settings page query a
site through.

tool_queries.py, the tools, the watcher and the settings page call only
what's defined here. JsonApiBackend (client.py) is today's implementation;
R4 adds one over the Drupal MCP module. The content-model cache (the
discovered schema, the installer's type selection, retrying a failed
discovery) is the same for every backend, so it lives here: a backend
supplies get_schema() and the queries.

Node dicts, as every query returns them: ``id``, ``title``, ``type``,
``created``, ``changed`` (ISO 8601), ``path`` (alias or None), ``body``
(plain, speakable text) and ``summary``.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from types import TracebackType
from typing import Any

from .config import Config
from .schema import Schema, fallback_schema, select_types

logger = logging.getLogger(__name__)


class DreachySiteError(Exception):
    """The Drupal site is unreachable or returned an error response."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        # The HTTP status, when the site answered with an error one.
        self.status = status


class Backend(ABC):
    """A site Dreachy talks about. Sync: tools call it via asyncio.to_thread()."""

    def __init__(self, config: Config, *, auto_discover: bool = False) -> None:
        self.config = config
        # auto_discover: queries retry a failed discovery (see _types). Off
        # by default, so a backend built for a test, or before a site URL is
        # configured, never sends discovery requests of its own accord.
        self._auto_discover = auto_discover
        self._full_schema: Schema = fallback_schema(config.content_types)
        self.schema_discovered = False
        self._next_discovery_at = 0.0
        self._discovery_lock = threading.Lock()

    def __enter__(self) -> Backend:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    @abstractmethod
    def close(self) -> None:
        """Release connections."""

    # -- content model ----------------------------------------------------

    @property
    def full_schema(self) -> Schema:
        """Every type Dreachy knows of, before the installer's selection."""
        return dict(self._full_schema)

    @property
    def schema(self) -> Schema:
        """The types Dreachy talks about. Never touches the network."""
        return select_types(self._full_schema, self.config.enabled_types)

    @abstractmethod
    def get_schema(self) -> Schema:
        """Discover the site's content types and their text fields.

        Raises DreachySiteError when the site can't be read.
        """

    def refresh_schema(self) -> bool:
        """Replace the cached schema with a fresh discovery.

        On failure keeps the current schema (the fallback, or the last one
        discovered) and returns False; never raises DreachySiteError.
        """
        with self._discovery_lock:
            return self._refresh_locked()

    def _refresh_locked(self) -> bool:
        try:
            schema = self.get_schema()
        except DreachySiteError as exc:
            return self._discovery_failed(str(exc))
        if not schema:
            return self._discovery_failed("no readable content type has text fields")
        self._full_schema = schema
        self.schema_discovered = True
        return True

    def _discovery_failed(self, reason: str) -> bool:
        logger.warning(
            "Couldn't discover the site's content types (%s); using %s", reason, ", ".join(self._full_schema)
        )
        self._next_discovery_at = time.monotonic() + self.config.schema_retry_seconds
        return False

    def _discovery_due(self) -> bool:
        return not self.schema_discovered and time.monotonic() >= self._next_discovery_at

    def _types(self) -> Schema:
        """The enabled schema, first retrying a failed discovery if one is due."""
        if self._auto_discover and self._discovery_due():
            with self._discovery_lock:
                # Checked again under the lock: callers that queued behind
                # another thread's discovery use its result, rather than
                # each running their own back to back.
                if self._discovery_due():
                    self._refresh_locked()
        return self.schema

    # -- queries ----------------------------------------------------------

    @abstractmethod
    def get_recent_nodes(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Newest content across the enabled types, newest first.

        The watcher polls this with limit=1.
        """

    @abstractmethod
    def find_content(self, keyword: str, content_type: str | None = None) -> list[dict[str, Any]]:
        """Title keyword search across the enabled types, newest first.

        An unknown or disabled content_type searches every enabled type.
        """

    @abstractmethod
    def get_article(self, title_or_path: str) -> dict[str, Any] | None:
        """One node, by URL path alias or exact title; None if nothing matches."""

    def get_site_pulse(self) -> dict[str, Any]:
        """Node counts and latest activity timestamp.

        The count is capped at ``config.pulse_sample_limit`` — core JSON:API
        doesn't expose a cheap collection total, so this is an explicit
        approximation suited to a small demo site, not a true count.
        """
        nodes = self.get_recent_nodes(limit=self.config.pulse_sample_limit)
        return {
            "node_count": len(nodes),
            "latest_node_created": nodes[0]["created"] if nodes else None,
        }
```

- [ ] **Step 4: Make `client.py` the JSON:API implementation**

In `dreachy/client.py`:

(a) Module docstring, first paragraph: replace `DrupalClient — thin sync wrapper over drupal-api-client's JsonApiClient.` with `JsonApiBackend — the Backend (backend.py) over Drupal's JSON:API, via drupal-api-client's JsonApiClient.`

(b) Imports:
- Delete `import threading`, `import time` and `from types import TracebackType`.
- Replace `from .schema import Schema, TypeSchema, build_schema, fallback_schema, select_types` with:

```python
from .backend import Backend, DreachySiteError
from .schema import Schema, TypeSchema, build_schema
```

Also add `__all__ = ["DreachySiteError", "DrupalClient", "JsonApiBackend"]` after the `logger = ...` line. `DreachySiteError` is re-exported because tests and tools import it from here.

(c) Delete the `class DreachySiteError(Exception): ...` block. It now lives in `backend.py`.

(d) Replace from `class DrupalClient:` through the end of `close()` with:

```python
class JsonApiBackend(Backend):
    """The Backend over JSON:API: anonymous by default."""

    def __init__(
        self, config: Config, *, http_client: httpx.Client | None = None, auto_discover: bool = False
    ) -> None:
        super().__init__(config, auto_discover=auto_discover)
        # No cache: InMemoryCache has no TTL, and every read below passes
        # disable_cache=True — a long-lived client (e.g. the tools' shared
        # singleton) would otherwise never see content published after its
        # first query of a given shape, which silently broke the watcher.
        self._client = JsonApiClient(
            config.base_url,
            timeout=config.request_timeout,
            default_locale=config.default_locale,
            http_client=http_client,
        )

    def close(self) -> None:
        self._client.close()
```

(e) Delete these members, which moved to `Backend`: the `full_schema` and `schema` properties, `refresh_schema`, `_refresh_locked`, `_discovery_failed`, `_discovery_due`, `_types` and `get_site_pulse`. Keep `get_schema` and its three helpers, `_get_collection`, and the three queries.

(f) At the end of the file:

```python
# The pre-R2 name: existing callers and tests keep working.
DrupalClient = JsonApiBackend
```

- [ ] **Step 5: Type the callers on `Backend`**

- `dreachy/tool_queries.py`:
  - Change `from .client import DrupalClient` to `from .backend import Backend`.
  - Change the four `client: DrupalClient` annotations to `client: Backend`.
  - In the module docstring, change "an already-configured DrupalClient" to "an already-configured Backend".
- `dreachy/tools/_shared.py`:
  - Docstring line 1 becomes `"""Shared Backend singleton for the Dreachy tool files.`
  - Add `from dreachy.backend import Backend`.
  - Change `_client: DrupalClient | None` and `def get_client() -> DrupalClient:` to use `Backend`.
  - Construct `JsonApiBackend` instead of `DrupalClient`, importing it with `from dreachy.client import JsonApiBackend`.
- `dreachy/tools/drupal_watch_site.py`:
  - Change `from dreachy.client import DreachySiteError, DrupalClient` to `from dreachy.backend import Backend, DreachySiteError`.
  - Change both `client: DrupalClient` annotations to `client: Backend`.
- `dreachy/tools/drupal_find_content.py`: in the comment, `DrupalClient.find_content` → `Backend.find_content`.
- `tests/test_discovery.py`:
  - Change `import dreachy.client as client_module` to `import dreachy.backend as backend_module`.
  - Change both `monkeypatch.setattr(client_module, "time", ...)` calls to `monkeypatch.setattr(backend_module, "time", ...)`. The retry logic they clock moved to `backend.py`.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest -q`
Expected: `105 passed` (97 plus 8 new). Existing tests pass unchanged, apart from the two patch targets.

- [ ] **Step 7: Commit**

```bash
git add dreachy/backend.py dreachy/client.py dreachy/tool_queries.py dreachy/tools tests/_fake_backend.py tests/test_tools_on_backend.py tests/test_discovery.py
git commit -m "refactor: Backend seam; the JSON:API client becomes JsonApiBackend

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Published content only, with an explicit opt-in

**Files:**
- Modify: `dreachy/backend.py` (the three read methods gain `include_unpublished`)
- Modify: `dreachy/client.py` (`_published`, `_sample_attributes`, the three reads)
- Modify: `tests/_fake_site.py` (`FakeSite._collection` honours `filter[status]`)
- Modify: `tests/_fake_backend.py` (records `include_unpublished`)
- Test: `tests/test_discovery.py` (append), `tests/test_tools_on_backend.py` (append)

**Interfaces:**
- Consumes: `Backend`, `JsonApiBackend`, `FakeBackend` (Task 1)
- Produces:
  - `get_recent_nodes(limit=None, *, include_unpublished=False)`
  - `find_content(keyword, content_type=None, *, include_unpublished=False)`
  - `get_article(title_or_path, *, include_unpublished=False)`
  - Published-only unless `include_unpublished=True`. Discovery always samples published content only. No tool passes the flag (R3's editorial tools will).
  - `FakeBackend.unpublished_requests: list[bool]`

- [ ] **Step 1: Teach `FakeSite` the status filter, and `FakeBackend` to record the flag**

In `tests/_fake_site.py` `FakeSite._collection`, after the `data = sorted(...)` line, add:

```python
        if params.get("filter[status]") == "1":
            data = [r for r in data if r["attributes"].get("status", True)]
```

In `tests/_fake_backend.py`, replace the three query methods of `FakeBackend`, and add `self.unpublished_requests: list[bool] = []` to its `__init__`:

```python
    def _recent(self, limit: int) -> list[dict[str, Any]]:
        return sorted(self.nodes, key=lambda n: n["created"], reverse=True)[:limit]

    def get_recent_nodes(self, limit: int | None = None, *, include_unpublished: bool = False) -> list[dict[str, Any]]:
        self._check()
        self.unpublished_requests.append(include_unpublished)
        return self._recent(limit or self.config.whats_new_limit)

    def find_content(
        self, keyword: str, content_type: str | None = None, *, include_unpublished: bool = False
    ) -> list[dict[str, Any]]:
        self._check()
        self.unpublished_requests.append(include_unpublished)
        return [
            n
            for n in self._recent(len(self.nodes) or 1)
            if keyword.lower() in n["title"].lower() and content_type in (None, n["type"])
        ]

    def get_article(self, title_or_path: str, *, include_unpublished: bool = False) -> dict[str, Any] | None:
        self._check()
        self.unpublished_requests.append(include_unpublished)
        return next((n for n in self.nodes if title_or_path in (n["title"], n["path"])), None)
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_discovery.py`, and add `node` and `formatted` to its `from _fake_site import ...` line:

```python
# ---------------------------------------------------------------------------
# Published content only, unless a caller explicitly asks otherwise. A
# logged-in Dreachy (R2) may be able to view drafts, and no tool may read
# them aloud or react to them; R3's editorial tools opt in deliberately.
# ---------------------------------------------------------------------------

_DRAFT = node(
    "news_item", "d1", "Draft: council scandal", "2026-09-29T09:00:00+00:00",
    status=False, body=formatted("<p>Not for publication.</p>"),
)
_WITH_DRAFT = {**NEWS_NODES, "news_item": [*NEWS_NODES["news_item"], _DRAFT]}


def test_logged_in_reads_skip_unpublished_content() -> None:
    with FakeSite(_WITH_DRAFT, labels=NEWS_LABELS).client(auto_discover=True) as client:
        titles = [n["title"] for n in client.get_recent_nodes(limit=10)]

    assert "Draft: council scandal" not in titles


def test_search_skips_unpublished_content() -> None:
    with FakeSite(_WITH_DRAFT, labels=NEWS_LABELS).client(auto_discover=True) as client:
        assert client.find_content("scandal") == []


def test_reading_an_unpublished_node_by_title_finds_nothing() -> None:
    with FakeSite(_WITH_DRAFT, labels=NEWS_LABELS).client(auto_discover=True) as client:
        assert client.get_article("Draft: council scandal") is None


def test_reading_an_unpublished_node_by_path_finds_nothing() -> None:
    with FakeSite(_WITH_DRAFT, labels=NEWS_LABELS).client(auto_discover=True) as client:
        assert client.get_article("/news_item/d1") is None


def test_include_unpublished_is_an_explicit_opt_in() -> None:
    with FakeSite(_WITH_DRAFT, labels=NEWS_LABELS).client(auto_discover=True) as client:
        titles = [n["title"] for n in client.get_recent_nodes(limit=10, include_unpublished=True)]
        by_path = client.get_article("/news_item/d1", include_unpublished=True)

    assert "Draft: council scandal" in titles
    assert by_path["title"] == "Draft: council scandal"
```

Append to `tests/test_tools_on_backend.py`:

```python
def test_no_tool_asks_for_unpublished_content() -> None:
    backend = shared._client
    _call(DrupalWhatsNew())
    _call(DrupalFindContent(), keyword="park")
    _call(DrupalSitePulse())
    _call(DrupalReadArticle(), title_or_path="Harvest fair")
    asyncio.run(watch_module._latest_created(backend))

    assert backend.unpublished_requests and not any(backend.unpublished_requests)
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_discovery.py tests/test_tools_on_backend.py -q`
Expected:
- The 4 skip tests FAIL: the draft appears.
- `test_include_unpublished_is_an_explicit_opt_in` FAILS with `TypeError: ... unexpected keyword argument 'include_unpublished'`.
- `test_no_tool_asks_for_unpublished_content` already PASSES. It's a guard: no tool passes the flag today, and the test pins that none ever does.

- [ ] **Step 4: Implement**

`dreachy/backend.py`: give the three abstract read methods the keyword-only flag, and add a line to each docstring:

```python
    @abstractmethod
    def get_recent_nodes(self, limit: int | None = None, *, include_unpublished: bool = False) -> list[dict[str, Any]]:
        """Newest content across the enabled types, newest first.

        The watcher polls this with limit=1. Published content only unless
        include_unpublished — which no tool sets (R3's editorial tools will).
        """

    @abstractmethod
    def find_content(
        self, keyword: str, content_type: str | None = None, *, include_unpublished: bool = False
    ) -> list[dict[str, Any]]:
        """Title keyword search across the enabled types, newest first.

        An unknown or disabled content_type searches every enabled type.
        Published content only unless include_unpublished.
        """

    @abstractmethod
    def get_article(self, title_or_path: str, *, include_unpublished: bool = False) -> dict[str, Any] | None:
        """One node, by URL path alias or exact title; None if nothing matches.

        Published content only unless include_unpublished.
        """
```

`dreachy/client.py`: add a helper below `_node_to_dict`:

```python
def _published(params: DrupalJsonApiParams, include_unpublished: bool = False) -> DrupalJsonApiParams:
    """Published content only, unless a caller explicitly opts in.

    Anonymous access sees only published nodes anyway; a logged-in Dreachy
    (R2) may be able to view drafts, and no tool may read them aloud, search
    them or react to them. R3's editorial tools opt in deliberately.
    """
    return params if include_unpublished else params.add_filter("status", 1)
```

- `_sample_attributes`: wrap the params in `_published(...)`. Discovery always samples published content.
- `get_recent_nodes(self, limit: int | None = None, *, include_unpublished: bool = False)`: `params = _published(DrupalJsonApiParams().add_sort(...).add_page_limit(limit), include_unpublished)`.
- `find_content(self, keyword, content_type=None, *, include_unpublished: bool = False)`: wrap its params chain the same way.
- `get_article(self, title_or_path, *, include_unpublished: bool = False)`:
  - wrap the title-search params the same way;
  - change the path branch's `if bundle in types:` to:

```python
            published = resource["data"]["attributes"].get("status", True)
            if bundle in types and (published or include_unpublished):
```

  - extend the comment below it: `(a disabled type, a taxonomy term, an unpublished node)`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q`
Expected: `111 passed`. R1's request-path assertions still hold, because they compare paths, not query strings.

- [ ] **Step 6: Commit**

```bash
git add dreachy/backend.py dreachy/client.py tests/_fake_site.py tests/_fake_backend.py tests/test_discovery.py tests/test_tools_on_backend.py
git commit -m "feat: published content only, with an explicit include_unpublished opt-in

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Login settings in `Config`

**Files:**
- Modify: `dreachy/config.py`
- Test: `tests/test_config.py` (new)

**Interfaces:**
- Produces:
  - `Config.auth: str = "none"`
  - `Config.oauth_client_id: str = ""`
  - `Config.oauth_client_secret: str` (`repr=False`)
  - `Config.uses_oauth -> bool`
  - `Config.from_env()` reads `DREACHY_AUTH`, `DREACHY_OAUTH_CLIENT_ID` and `DREACHY_OAUTH_CLIENT_SECRET`
  - `AUTH_MODES = ("none", "oauth")`

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py`:

```python
"""Config.from_env: the installer's settings, including the optional site login."""

from __future__ import annotations

import logging

import pytest

from dreachy.config import Config

_SECRET = "s3cret-value-never-shown"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("DREACHY_AUTH", "DREACHY_OAUTH_CLIENT_ID", "DREACHY_OAUTH_CLIENT_SECRET"):
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)


def test_anonymous_is_the_default() -> None:
    config = Config.from_env()

    assert (config.auth, config.uses_oauth) == ("none", False)


def test_oauth_with_id_and_secret_is_used(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_AUTH", "oauth")
    monkeypatch.setenv("DREACHY_OAUTH_CLIENT_ID", "dreachy")
    monkeypatch.setenv("DREACHY_OAUTH_CLIENT_SECRET", _SECRET)

    config = Config.from_env()

    assert config.uses_oauth is True
    assert (config.oauth_client_id, config.oauth_client_secret) == ("dreachy", _SECRET)


def test_oauth_without_a_secret_falls_back_to_anonymous(monkeypatch, caplog) -> None:
    monkeypatch.setenv("DREACHY_AUTH", "oauth")
    monkeypatch.setenv("DREACHY_OAUTH_CLIENT_ID", "dreachy")

    with caplog.at_level(logging.WARNING):
        config = Config.from_env()

    assert config.uses_oauth is False
    assert "anonymous" in caplog.text


def test_an_unknown_auth_mode_means_anonymous(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_AUTH", "kerberos")

    assert Config.from_env().auth == "none"


def test_config_repr_never_shows_the_client_secret(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_AUTH", "oauth")
    monkeypatch.setenv("DREACHY_OAUTH_CLIENT_ID", "dreachy")
    monkeypatch.setenv("DREACHY_OAUTH_CLIENT_SECRET", _SECRET)

    assert _SECRET not in repr(Config.from_env())
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL. `AttributeError: 'Config' object has no attribute 'auth'` (and `uses_oauth`); the repr test fails with `AttributeError` too.

- [ ] **Step 3: Implement**

In `dreachy/config.py`:
- Change `from dataclasses import dataclass` to `from dataclasses import dataclass, field`.
- Add `import logging` and, below the imports:

```python
logger = logging.getLogger(__name__)

AUTH_MODES = ("none", "oauth")
```

Add a section to `Config` after the site-connection fields:

```python
    # ---------------------------------------------------------------------------
    # Site login (R2) — optional; anonymous is the default
    # ---------------------------------------------------------------------------
    # "none": anonymous. "oauth": OAuth2 client credentials, handled by
    # drupal-api-client — used only when both the id and the secret are set.
    auth: str = "none"
    oauth_client_id: str = ""
    # repr=False: Configs get logged and passed around; the secret mustn't
    # ride along (spec Invariants: secrets confined to the client/auth layer).
    oauth_client_secret: str = field(default="", repr=False)
```

Add below the fields, before `from_env`:

```python
    @property
    def uses_oauth(self) -> bool:
        return self.auth == "oauth" and bool(self.oauth_client_id and self.oauth_client_secret)
```

In `from_env`, before `return config`:

```python
        auth = os.environ.get("DREACHY_AUTH", "none").strip().lower() or "none"
        if auth not in AUTH_MODES:
            logger.warning("Unknown DREACHY_AUTH %r; using anonymous access", auth)
            auth = "none"
        config.auth = auth
        config.oauth_client_id = os.environ.get("DREACHY_OAUTH_CLIENT_ID", "").strip()
        config.oauth_client_secret = os.environ.get("DREACHY_OAUTH_CLIENT_SECRET", "").strip()
        if auth == "oauth" and not config.uses_oauth:
            logger.warning("OAuth is selected but the client ID or secret is missing; using anonymous access")
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q`
Expected: `116 passed`

- [ ] **Step 5: Commit**

```bash
git add dreachy/config.py tests/test_config.py
git commit -m "feat: optional site-login settings in Config

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Settings page — "Advanced: site login"

**Files:**
- Modify: `dreachy/main.py` (new `_AuthPayload`, `GET`/`POST /api/auth`)
- Modify: `dreachy/static/index.html` (collapsed Advanced section, JS)
- Test: `tests/test_settings_routes.py` (append; add the three auth keys to the fixture's env loop)

**Interfaces:**
- Consumes: `AUTH_MODES` (Task 3), `reset_client` (R1)
- Produces:
  - `GET /api/auth` → `{"auth": "none"|"oauth", "client_id": str, "client_secret_set": bool, "active": bool}`
  - `POST /api/auth` takes `{auth, client_id, client_secret: str|null, clear_client_secret: bool}` and returns the same shape as `GET`

- [ ] **Step 1: Write the failing tests**

In `tests/test_settings_routes.py`, extend the fixture's env loop tuple with `"DREACHY_AUTH", "DREACHY_OAUTH_CLIENT_ID", "DREACHY_OAUTH_CLIENT_SECRET"`. Then append:

```python
# ---------------------------------------------------------------------------
# Advanced: site login. The secret is write-only: saved to the owner-only
# .env, never sent back to the page.
# ---------------------------------------------------------------------------

_SECRET = "s3cret-value-never-shown"


def _save_login(client, **fields):
    body = {"auth": "oauth", "client_id": "dreachy", "client_secret": None, "clear_client_secret": False, **fields}
    return client.post("/api/auth", json=body)


def test_get_auth_defaults_to_anonymous() -> None:
    assert _make_client().get("/api/auth").json() == {
        "auth": "none", "client_id": "", "client_secret_set": False, "active": False,
    }


def test_get_auth_never_returns_the_secret(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_AUTH", "oauth")
    monkeypatch.setenv("DREACHY_OAUTH_CLIENT_ID", "dreachy")
    monkeypatch.setenv("DREACHY_OAUTH_CLIENT_SECRET", _SECRET)

    resp = _make_client().get("/api/auth")

    assert _SECRET not in resp.text
    assert resp.json() == {"auth": "oauth", "client_id": "dreachy", "client_secret_set": True, "active": True}


def test_saving_a_login_stores_it_and_drops_the_cached_client() -> None:
    shared._client = "sentinel-old-client"

    resp = _save_login(_make_client(), client_secret=_SECRET)

    assert _SECRET not in resp.text
    assert resp.json()["active"] is True
    assert os.environ["DREACHY_OAUTH_CLIENT_SECRET"] == _SECRET
    assert _SECRET in (dreachy_main._instance_path() / ".env").read_text()
    assert shared._client is None


def test_saving_a_secret_makes_the_env_owner_only() -> None:
    _save_login(_make_client(), client_secret=_SECRET)

    assert (dreachy_main._instance_path() / ".env").stat().st_mode & 0o777 == 0o600


def test_saving_with_a_blank_secret_keeps_the_saved_one(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_OAUTH_CLIENT_SECRET", _SECRET)

    _save_login(_make_client(), client_secret="")

    assert os.environ["DREACHY_OAUTH_CLIENT_SECRET"] == _SECRET


def test_clearing_the_secret_removes_it(monkeypatch) -> None:
    client = _make_client()
    _save_login(client, client_secret=_SECRET)

    resp = _save_login(client, clear_client_secret=True)

    assert resp.json()["client_secret_set"] is False
    assert "DREACHY_OAUTH_CLIENT_SECRET" not in os.environ
    assert _SECRET not in (dreachy_main._instance_path() / ".env").read_text()


def test_get_auth_reports_an_incomplete_login(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_AUTH", "oauth")
    monkeypatch.setenv("DREACHY_OAUTH_CLIENT_ID", "dreachy")

    assert _make_client().get("/api/auth").json()["active"] is False
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_settings_routes.py -q`
Expected: the 7 new tests FAIL with `404` from `/api/auth`. The existing 27 pass.

- [ ] **Step 3: Implement the routes**

In `dreachy/main.py`:
- Add `from typing import Literal` to the imports.
- Change `from dreachy.config import Config, parse_types` to also import `AUTH_MODES`.
- Add after `_ConfigPayload`:

```python
class _AuthPayload(BaseModel):
    auth: Literal["none", "oauth"] = "none"
    client_id: str = ""
    # None or "" = keep the saved secret. It's never sent back to the page,
    # so a blank field means "unchanged", not "remove".
    client_secret: str | None = None
    clear_client_secret: bool = False


def _auth_status() -> dict:
    auth = os.environ.get("DREACHY_AUTH", "none") or "none"
    client_id = os.environ.get("DREACHY_OAUTH_CLIENT_ID", "")
    secret_set = bool(os.environ.get("DREACHY_OAUTH_CLIENT_SECRET"))
    return {
        "auth": auth if auth in AUTH_MODES else "none",
        "client_id": client_id,
        "client_secret_set": secret_set,
        "active": auth == "oauth" and bool(client_id) and secret_set,
    }
```

Inside `_register_settings_routes`, after the `/api/schema` route:

```python
    @settings_app.get("/api/auth")
    def get_auth() -> dict:
        return _auth_status()

    @settings_app.post("/api/auth")
    def save_auth(payload: _AuthPayload) -> dict:
        env_path = _instance_path() / ".env"
        updates = {"DREACHY_AUTH": payload.auth, "DREACHY_OAUTH_CLIENT_ID": payload.client_id.strip()}
        if payload.client_secret and not payload.clear_client_secret:
            updates["DREACHY_OAUTH_CLIENT_SECRET"] = payload.client_secret.strip()
        for key, value in updates.items():
            dotenv.set_key(str(env_path), key, value)
            os.environ[key] = value
        if payload.clear_client_secret:
            env_path.touch()
            dotenv.unset_key(str(env_path), "DREACHY_OAUTH_CLIENT_SECRET")
            os.environ.pop("DREACHY_OAUTH_CLIENT_SECRET", None)
        # The .env may now hold a client secret (it already holds HF_TOKEN):
        # owner-only.
        env_path.chmod(0o600)
        reset_client()  # the next request logs in with the new settings
        return _auth_status()
```

Also update the `_register_settings_routes` docstring to list `GET`/`POST /api/auth`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q`
Expected: `123 passed`

- [ ] **Step 5: The Advanced section on the page**

In `dreachy/static/index.html`:

(a) CSS, before `button {`:

```css
		details {
			margin: 0 0 1.5rem 0;
		}

		summary {
			font-weight: 600;
			cursor: pointer;
			margin-bottom: 0.8rem;
		}

		input[type="text"],
		input[type="password"],
		select {
			width: 100%;
			padding: 0.6rem 0.75rem;
			border: 2px solid #e2e8f0;
			border-radius: 8px;
			font-size: 0.95rem;
			font-family: inherit;
		}
```

(b) HTML: insert before the extra-instructions `<fieldset>`:

```html
		<details id="advanced">
			<summary>Advanced: site login</summary>
			<fieldset>
				<label for="auth_mode">Login</label>
				<select id="auth_mode">
					<option value="none">None — anonymous (default)</option>
					<option value="oauth">OAuth client credentials</option>
				</select>
				<p class="hint">Only needed if the content Dreachy should read isn't public. See
					<code>docs/drupal-setup.md</code> for creating the OAuth client on the site.</p>
			</fieldset>
			<fieldset>
				<label for="oauth_client_id">Client ID</label>
				<input type="text" id="oauth_client_id" autocomplete="off" />
			</fieldset>
			<fieldset>
				<label for="oauth_client_secret">Client secret</label>
				<input type="password" id="oauth_client_secret" autocomplete="new-password" placeholder="Not set" />
				<label class="type-option"><input type="checkbox" id="oauth_clear_secret" /> Remove the saved
					secret</label>
				<p class="hint">Write-only: a saved secret is never shown here. Leave the field blank to keep it.</p>
			</fieldset>
			<p class="hint" id="auth_status"></p>
		</details>
```

(c) JS: add after the `let loadedLocale = "";` line:

```js
		const authModeInput = document.getElementById("auth_mode");
		const clientIdInput = document.getElementById("oauth_client_id");
		const clientSecretInput = document.getElementById("oauth_client_secret");
		const clearSecretInput = document.getElementById("oauth_clear_secret");
		const authStatusEl = document.getElementById("auth_status");

		async function loadAuth() {
			try {
				const data = await (await fetch("/api/auth")).json();
				authModeInput.value = data.auth;
				clientIdInput.value = data.client_id;
				clientSecretInput.value = "";
				clearSecretInput.checked = false;
				clientSecretInput.placeholder = data.client_secret_set ? "Saved — leave blank to keep it" : "Not set";
				authStatusEl.textContent = data.auth === "oauth" && !data.active
					? "OAuth is selected but the client ID or secret is missing, so Dreachy is using anonymous access."
					: "";
				if (data.auth === "oauth") document.getElementById("advanced").open = true;
			} catch (e) {
				authStatusEl.textContent = "Couldn't load the login settings.";
			}
		}

		async function saveAuth() {
			const resp = await fetch("/api/auth", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					auth: authModeInput.value,
					client_id: clientIdInput.value,
					client_secret: clientSecretInput.value || null,
					clear_client_secret: clearSecretInput.checked,
				}),
			});
			if (!resp.ok) throw new Error("couldn't save the login settings");
		}
```

In the submit handler, immediately after `if (!resp.ok) throw new Error(data.detail || "Save failed");`, add:

```js
				await saveAuth();
				await loadAuth();
```

Replace the final `loadConfig();` / `loadTypes();` pair with:

```js
		loadConfig();
		loadAuth();
		loadTypes();
```

- [ ] **Step 6: Check the page over HTTP**

Same approach as R1 Task 4, with the scratchpad `settings_check.py` server.
- `GET /api/auth` with nothing saved matches the default.
- Save a login, then check that `GET` shows `client_secret_set: true` and that the secret appears in neither response.
- Save again with `client_secret: null`, and check the secret is kept.
- Clear it, and check it's gone.
- The page's script passes `node --check`.

Vincenzo does the browser pass at review.

- [ ] **Step 7: Commit**

```bash
git add dreachy/main.py dreachy/static/index.html tests/test_settings_routes.py
git commit -m "feat: settings page Advanced section for the optional site login

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `docs/drupal-setup.md`, README, `.env.example`

**Files:**
- Create: `docs/drupal-setup.md`
- Modify: `README.md` (Requirements, Configuration, Development), `.env.example`

- [ ] **Step 1: Research, and cite what you find**

Check each of these against the current module documentation, and record the module version and links at the top of the doc:
- `simple_oauth` on drupal.org: the release line for Drupal 11 (6.x at the time of writing).
- How to enable the client-credentials grant.
- How to create a consumer, and whether 6.x needs a scope entity and a default user for client credentials.
- How keys are generated.

Where the 6.x UI and the documentation disagree, the UI on the local DDEV site (`reachy/dreachy`, Drupal 11.4.4) wins. Only read it; don't install anything there.

- [ ] **Step 2: Write `docs/drupal-setup.md`** with these sections, each a numbered walkthrough:

1. **When you need this.** Only for content anonymous visitors can't see, such as a private site or intranet. A public site needs none of it.
2. **Install and enable `simple_oauth`.** The Composer and Drush commands, and key generation.
3. **A dedicated `dreachy` role and user.** Least privilege for R2, which only reads: `access content` and nothing else. Why: the published-only filter means Dreachy never reads drafts even if the role can see them, but R2 doesn't need them, so don't grant them.
4. **The OAuth client (consumer).** Client-credentials grant, the `dreachy` user and scope, and a secret. The secret goes into Dreachy's settings page (Advanced: site login), and nowhere else.
5. **Check it works (the R2 live check).** Remove `access content` from Anonymous. Then:
   - Dreachy with login **None** can't reach the content: the robot says it can't reach the site.
   - Dreachy with OAuth answers normally.
   - The site's access log shows `Authorization: Bearer` on Dreachy's requests. The watchdog log shows the `dreachy` user.
6. **Revoking.** Delete the consumer or rotate its secret. Dreachy then says the site refused its credentials until the settings page has the new secret.

- [ ] **Step 3: README and `.env.example`**

README:
- **Requirements:** change "Anonymous read access to whichever content types you expose (Dreachy doesn't authenticate …)" to: "**Anonymous read access** to the content Dreachy should talk about — or, for a private site, an OAuth client (see [`docs/drupal-setup.md`](docs/drupal-setup.md)). Dreachy only ever reads published content."
- **Configuration:** add after the Content types bullet: "**Advanced: site login** (optional) — OAuth client credentials for a site whose content isn't public. The secret is write-only: once saved it's never shown again, and a blank field keeps it. Takes effect immediately."
- **Development:** add `backend.py` ("the `Backend` interface the tools query through; `client.py`'s `JsonApiBackend` implements it") and `auth.py` (added in Task 6).

`.env.example`, after the content-types block:

```
# --- Optional: site login (private sites only) ---
# Normally set from the settings page's "Advanced: site login". Leave unset
# for a public site. The secret belongs in the instance .env only — never in
# this file, the profile, or the repo.
# DREACHY_AUTH=oauth
# DREACHY_OAUTH_CLIENT_ID=dreachy
# DREACHY_OAUTH_CLIENT_SECRET=
```

- [ ] **Step 4: Run the tests** (docs only; confirms nothing else moved)

Run: `uv run pytest -q`
Expected: `123 passed`

- [ ] **Step 5: Commit**

```bash
git add docs/drupal-setup.md README.md .env.example
git commit -m "docs: Drupal-side setup for the optional site login

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: OAuth provider wiring (needs drupal-api-client ≥0.3.1) — LAST

**Files:**
- Create: `dreachy/auth.py`
- Modify: `dreachy/backend.py` (add `DreachyAuthError`)
- Modify: `dreachy/client.py` (pass `authentication`, map login errors)
- Modify: `dreachy/profile/dreachy/instructions.txt` (FAILURE HANDLING line)
- Modify: `pyproject.toml`, `uv.lock` (`drupal-api-client>=0.3.1`), `CHANGELOG.md`, version `0.3.0`
- Test: `tests/test_auth.py` (new)

**Interfaces:**
- Consumes: `Config.uses_oauth`, `oauth_client_id` and `oauth_client_secret` (Task 3); `JsonApiBackend` (Task 1); the 0.3.1 contract above
- Produces:
  - `dreachy.auth.library_authentication(config) -> OAuthAuth | None`
  - `dreachy.auth.TOKEN_REFRESH_MARGIN_SECONDS = 60`
  - `dreachy.backend.DreachyAuthError(DreachySiteError)`

- [ ] **Step 1: Check the released 0.3.1 against the contract**

Run:

```bash
uv add "drupal-api-client>=0.3.1"
uv run python -c "import inspect, drupal_api_client as d; print(d.__version__ if hasattr(d, '__version__') else ''); print(inspect.signature(d.JsonApiClient.__init__))"
```

Read 0.3.1's CHANGELOG entry. If the refresh-margin keyword isn't `token_refresh_margin`, or the 401 retry differs from the contract, use the real names in Steps 2–4 and ledger a `Ruling:`. If 0.3.1 isn't released yet, stop here: Tasks 1–5 are complete without it.

- [ ] **Step 2: Write the failing tests**

`tests/test_auth.py`:

```python
"""The optional site login, end to end against a mocked private site: the
token endpoint, Bearer headers, drupal-api-client 0.3.1's refresh margin and
single 401 retry, and what a refused login looks like to the tools. The
secret must never reach an error message or the log."""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
from _fake_site import NEWS_LABELS, NEWS_NODES, FakeSite

import dreachy.tools._shared as shared
from dreachy.backend import DreachyAuthError, DreachySiteError
from dreachy.client import JsonApiBackend
from dreachy.config import Config
from dreachy.tools.drupal_whats_new import DrupalWhatsNew
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies

_SECRET = "s3cret-value-never-shown"


class PrivateSite:
    """FakeSite behind a login: every JSON:API or router request needs a
    Bearer token from POST /oauth/token (client credentials)."""

    def __init__(self, *, accept: bool = True, expires_in: int = 300) -> None:
        self.site = FakeSite(NEWS_NODES, labels=NEWS_LABELS)
        self.accept = accept
        self.expires_in = expires_in
        self.grants = 0
        self.valid_tokens: set[str] = set()
        self.seen_auth: list[str | None] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            if not self.accept or _SECRET not in request.content.decode():
                return httpx.Response(401, json={"error": "invalid_client"})
            self.grants += 1
            token = f"token-{self.grants}"
            self.valid_tokens.add(token)
            return httpx.Response(
                200, json={"access_token": token, "expires_in": self.expires_in, "token_type": "Bearer"}
            )
        auth = request.headers.get("Authorization")
        self.seen_auth.append(auth)
        if auth is None or auth.removeprefix("Bearer ") not in self.valid_tokens:
            return httpx.Response(401, json={"errors": [{"status": "401"}]})
        return self.site.handle(request)

    def backend(self, config: Config | None = None) -> JsonApiBackend:
        config = config or _oauth_config()
        return JsonApiBackend(config, http_client=httpx.Client(transport=httpx.MockTransport(self.handle)))


def _oauth_config(**overrides) -> Config:
    config = Config(auth="oauth", oauth_client_id="dreachy", oauth_client_secret=_SECRET)
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def test_anonymous_mode_sends_no_credentials_and_asks_for_no_token() -> None:
    private = PrivateSite()

    with private.backend(Config()) as backend:
        with pytest.raises(DreachySiteError):
            backend.get_recent_nodes()

    assert private.grants == 0
    assert private.seen_auth and set(private.seen_auth) == {None}


def test_a_logged_in_backend_reads_a_private_site() -> None:
    private = PrivateSite()

    with private.backend() as backend:
        assert backend.refresh_schema() is True
        titles = [n["title"] for n in backend.get_recent_nodes(limit=10)]

    assert "Council approves new park" in titles
    assert all(a and a.startswith("Bearer ") for a in private.seen_auth)


def test_a_token_is_reused_until_it_nears_expiry() -> None:
    private = PrivateSite(expires_in=300)

    with private.backend() as backend:
        backend.get_recent_nodes()
        backend.get_recent_nodes()

    assert private.grants == 1


def test_a_token_inside_the_refresh_margin_is_renewed_first() -> None:
    # 0.3.1 contract: refresh at < TOKEN_REFRESH_MARGIN_SECONDS (60) remaining.
    private = PrivateSite(expires_in=30)

    with private.backend() as backend:
        backend.get_recent_nodes()
        backend.get_recent_nodes()

    assert private.grants == 2


def test_a_revoked_token_is_renewed_once_and_the_request_retried() -> None:
    # 0.3.1 contract: one fresh token and one retry on 401.
    private = PrivateSite()

    with private.backend() as backend:
        backend.get_recent_nodes()
        private.valid_tokens.clear()  # the site revoked the token
        backend.get_recent_nodes()

    assert private.grants == 2


def test_a_refused_login_is_a_dreachy_auth_error() -> None:
    with PrivateSite(accept=False).backend() as backend:
        with pytest.raises(DreachyAuthError) as excinfo:
            backend.get_recent_nodes()

    assert "credentials" in str(excinfo.value)


def test_a_refused_login_never_reveals_the_secret(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        with PrivateSite(accept=False).backend() as backend:
            with pytest.raises(DreachyAuthError) as excinfo:
                backend.get_recent_nodes()

    assert _SECRET not in str(excinfo.value)
    assert _SECRET not in repr(excinfo.value)
    assert _SECRET not in caplog.text


def test_a_refused_login_fails_discovery_instead_of_skipping_types() -> None:
    with PrivateSite(accept=False).backend() as backend:
        assert backend.refresh_schema() is False
        assert backend.schema_discovered is False


def test_a_refused_login_reaches_the_tools_as_a_site_error() -> None:
    shared._client = PrivateSite(accept=False).backend()
    try:
        result = asyncio.run(DrupalWhatsNew()(ToolDependencies(reachy_mini=None, movement_manager=None)))
    finally:
        shared._client = None

    assert result["error"].startswith("I can't reach the site right now: ")
    assert "credentials" in result["error"]
    assert _SECRET not in result["error"]


def test_the_watcher_survives_a_refused_login() -> None:
    # DreachyAuthError is a DreachySiteError, which the watcher's loop
    # already catches and backs off on.
    assert issubclass(DreachyAuthError, DreachySiteError)
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_auth.py -q`
Expected: collection error, `ImportError: cannot import name 'DreachyAuthError'`

- [ ] **Step 4: Implement**

`dreachy/backend.py`, after `DreachySiteError`:

```python
class DreachyAuthError(DreachySiteError):
    """The site refused Dreachy's login (OAuth client credentials).

    A DreachySiteError, so the tools and the watcher already handle it: the
    tools report it, and the watcher backs off. The message never includes
    credentials.
    """
```

`dreachy/auth.py`:

```python
"""Which login Dreachy uses with the Drupal site.

drupal-api-client does the token handling itself (the grant, caching, and,
from 0.3.1, the refresh margin and one retry on 401). Dreachy only chooses
the provider from the installer's settings. The secret passes from Config
to the library here and goes nowhere else (spec Invariants).
"""

from __future__ import annotations

from drupal_api_client import OAuthAuth

from .config import Config

# Renew a token with less than this left, so a request never goes out
# carrying one that expires in flight.
TOKEN_REFRESH_MARGIN_SECONDS = 60.0


def library_authentication(config: Config) -> OAuthAuth | None:
    """The drupal-api-client authentication for *config*: None means anonymous."""
    if not config.uses_oauth:
        return None
    return OAuthAuth(client_id=config.oauth_client_id, client_secret=config.oauth_client_secret)
```

`dreachy/client.py`:
- Imports: add `AuthenticationError` to `from drupal_api_client import ...`; add `from .auth import TOKEN_REFRESH_MARGIN_SECONDS, library_authentication`; and change the backend import to `from .backend import Backend, DreachyAuthError, DreachySiteError`.
- Add a module-level message:

```python
# Never includes credentials; the tools prefix it with "I can't reach the site right now: ".
_CREDENTIALS_REFUSED = "the site refused Dreachy's credentials — check the site login on Dreachy's settings page"
```

- In `JsonApiBackend.__init__`, add to the `JsonApiClient(...)` call:

```python
            authentication=library_authentication(config),
            token_refresh_margin=TOKEN_REFRESH_MARGIN_SECONDS,
```

- Add a method mapping library and HTTP errors in one place:

```python
    def _site_error(self, exc: Exception, context: str) -> DreachySiteError:
        """The DreachySiteError for *exc*; a refused login becomes DreachyAuthError."""
        if isinstance(exc, AuthenticationError):
            return DreachyAuthError(_CREDENTIALS_REFUSED)
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            # Logged in and still 401 after the library's one retry: the login is bad.
            if status == 401 and self.config.uses_oauth:
                return DreachyAuthError(_CREDENTIALS_REFUSED, status=status)
            return DreachySiteError(str(exc), status=status)
        if isinstance(exc, httpx.HTTPError):
            return DreachySiteError(str(exc))
        return DreachySiteError(f"Unexpected response for {context}: {exc!r}")
```

- `_get_collection`: replace its three `except` clauses with a single `except (AuthenticationError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc: raise self._site_error(exc, resource_type) from exc`. Keep the captive-portal comment above it.
- `_node_bundles`: replace its `except` clause with:

```python
        except (AuthenticationError, httpx.HTTPError, ValueError, AttributeError) as exc:
            error = self._site_error(exc, "the JSON:API index")
            if type(error) is DreachySiteError:  # keep R1's wording for plain site failures
                error = DreachySiteError(f"JSON:API index unavailable: {exc}", status=error.status)
            raise error from exc
```
- `get_article`: change `except httpx.HTTPError as exc: raise DreachySiteError(str(exc)) from exc` to `except (AuthenticationError, httpx.HTTPError) as exc: raise self._site_error(exc, title_or_path) from exc`.
- `_sample_attributes`: at the start of its `except DreachySiteError as exc:` block, add:

```python
            if isinstance(exc, DreachyAuthError):
                raise  # a bad login fails discovery; it doesn't make a type "unreadable"
```

`dreachy/profile/dreachy/instructions.txt`, FAILURE HANDLING: append this paragraph.

```
If the error says the site refused your credentials, say you can't get into the site right now
and that whoever looks after you should check your login on the settings page — don't read out
technical details.
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q`
Expected: `133 passed` (123 plus 10). All earlier tests pass: without `DREACHY_AUTH`, `library_authentication` returns `None`, exactly as before.

- [ ] **Step 6: CHANGELOG, version, README**

- `pyproject.toml`: set `version = "0.3.0"`. `uv add` in Step 1 already raised the `drupal-api-client` floor to `>=0.3.1`.
- `CHANGELOG.md`: add at the top, below `# Changelog`:

```markdown
## 0.3.0 — unreleased

Backend seam and optional site login (`specs/backend-and-auth.md`, R2).

- Tools, the watcher and the settings page query a `Backend` interface;
  the JSON:API client is its first implementation (R4 adds MCP).
- Optional OAuth2 client-credentials login for private sites, from the
  settings page's Advanced section. The secret is write-only and stored
  only in the owner-only instance `.env`. Needs drupal-api-client 0.3.1.
- Dreachy reads published content only, logged in or not.
- A refused login is reported as such, not as a crash; the watcher keeps
  running.
```

- README Development: add `auth.py` ("chooses the site login; drupal-api-client handles tokens").

- [ ] **Step 7: Commit**

```bash
git add dreachy/auth.py dreachy/backend.py dreachy/client.py dreachy/profile/dreachy/instructions.txt tests/test_auth.py pyproject.toml uv.lock CHANGELOG.md README.md
git commit -m "feat: optional OAuth site login via drupal-api-client 0.3.1

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 8: Live checks (Vincenzo, spec R2.6)**
  - **Anonymous, public Umami sandbox:** unchanged. Run R1's live script again.
  - **Private site** (anonymous without `access content`), following `docs/drupal-setup.md`:
    - login **None**: the robot can't reach the site;
    - **OAuth**: normal answers;
    - `Authorization: Bearer` shows in the access log.
  - **Revoke** the consumer's secret: the robot says it can't get into the site, and the watcher keeps running.
  - The `docs/drupal-setup.md` walkthrough, from clean, on the sandbox.

**Stop here: R2 release boundary, for review.**
