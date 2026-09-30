# R1 — Schema-driven content model: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Drupal site with `news_item`/`body` works with Dreachy without editing Python. Umami keeps behaving exactly as it does today.

**Architecture:** A new pure module, `dreachy/schema.py`, decides which node types exist and which fields hold their text. `DrupalClient.get_schema()` fetches the evidence: the JSON:API index, node-type labels and sample nodes. The client caches the discovered schema, filters it by the installer's `DREACHY_TYPES` selection, and drives every query from it. `schema.FALLBACK_TYPES` (today's `_TEXT_FIELDS`) is used until a discovery succeeds. `main.py` loads the instance `.env` and warms the schema before the conversation app builds its tool specs, so the search tool's `content_type` enum lists the site's own types.

**Tech Stack:** Python 3.12, uv, pytest, httpx `MockTransport`, drupal-api-client 0.3.0, FastAPI (settings routes), plain HTML/JS settings page.

**Spec:** `specs/backend-and-auth.md` (R1 section plus Invariants)

**Repo:** `dreachy-app/` (standalone, `git@github.com:VincenzoGambino/dreachy.git`). Not `reachy-demos/src/dreachy`, which is the pre-packaging copy.

## Global Constraints

- Anonymous read-only JSON:API mode remains the zero-config default; a user who sets only the site URL gets today's behaviour (on Umami: identical types, fields, teasers and tool descriptions).
- Never fork or modify `reachy_mini_conversation_app`; the pin stays at `d44fc7191e34fd293e37d45f8a84b5a986e38a58`.
- The five existing tools keep their names and speech-facing behaviour.
- Secrets live only in the instance-path `.env`. R1 adds no secrets.
- Robot-audible behaviour changes get a line in the profile instructions, not hard-coded English strings.
- Tests: pytest, no network (HTTP mocked). Existing tests keep passing. Baseline: `uv run pytest -q` gives **44 passed**.
- `uv` only; documentation and comments in British English.
- Definition of done: tests green, README and settings page updated, a CHANGELOG.md entry.

## Spec vs code: divergences found before planning

The spec was written from the README. These are the places where the source differs. Items 1–6 shape this plan. Items marked *(amend)* need a line changed in the spec once you agree.

1. **Umami is hard-coded in more places than `config.py`/`_TEXT_FIELDS`.**
   - `drupal_find_content`'s `content_type` parameter is `enum: ["article","page","recipe"]`, so the LLM cannot even ask for `news_item`.
   - Tool descriptions for find, read and what's-new name the Umami types.
   - So do `instructions.txt`, `.env.example` and the settings-page hint.
   - This plan covers all of them (Task 5, Task 6).
2. **Core has no anonymous schema endpoint** *(amended in spec)*.
   - Field definitions (`field_config--field_config`) require `administer node fields`.
   - Node types (`node_type--node_type`) are viewable with `access content` on core 11.4.4 (checked in `NodeTypeAccessControlHandler`).
   - The chosen route is documented in `schema.py`: the JSON:API index for bundles, node types for labels (best effort), and sample nodes for text fields (inferred from value shape).
3. **The spec's output shape `{label_field, text_fields[]}` has no teaser role** *(amended in spec)*.
   - Today's code has one: recipe's `field_summary`.
   - Without it, Umami's mapping can't "fall out of the heuristics naturally".
   - The plan's `TypeSchema` is `{label, label_field, text_fields, summary_field}`.
4. **"Cache the schema at app start" needs an extra step.**
   - The conversation app builds tool specs (`main.py:281`, `initialize_tools`) before it loads the instance `.env` (`console.py:735`, in `launch()`).
   - So when `Dreachy.run()` starts, `DREACHY_BASE_URL` isn't in the environment yet.
   - Dreachy loads its own `.env` first (Task 3). Upstream later reloads the same file with `override=True`, which is harmless.
5. **Tool specs are frozen at start.**
   - "Refresh on settings save" refreshes the queries immediately.
   - The search tool's enum only updates on the next start.
   - A type disabled mid-session is handled by widening the search rather than failing (Task 2).
6. **The env → `Config` mapping lives in `tools/_shared.get_client()`, not `config.py`.** It moves to `Config.from_env()` (Task 3), since R2 adds four more variables.
7. **`tests/test_settings_routes.py` asserts the exact `GET /api/config` dict.** Discovered types therefore get a new `GET /api/schema` route instead of new keys on the existing one.
8. **`CHANGELOG.md` doesn't exist.** Task 6 creates it.

Noted for later releases (not planned here):

9. **R2 `Backend` signatures don't match what `tool_queries.py` calls.**
   - The actual calls are `get_recent_nodes(limit)`, `find_content(keyword, content_type|None)` and `get_article(title_or_path)`.
   - `get_article` resolves by path alias via Decoupled Router, or by exact title. It has no type or id to hand.
   - `get_site_pulse()` is derived from recent nodes.
   - So `get_node(type, id_or_uuid)` and `site_stats(types)` would be invented interfaces.
10. **R2 "ambient attention polls JSON:API directly": it doesn't.** The watcher already goes through the shared `DrupalClient` (`get_recent_nodes(limit=1)`).
11. **R2 OAuth partly exists already.** drupal-api-client 0.3.0 ships OAuth client-credentials: `OAuthAuth`, a token cache, and `POST {base}oauth/token`. The differences from the spec:
    - It refreshes at under 10 s remaining, not under 60 s.
    - It has no retry on 401.
    - It needs the secret handed to the library, which conflicts with "backends never see the secret".
    - R2 should decide whether to wrap it or replace it.
12. **R3 `tools.txt` isn't really static.** `_render_profile()` rewrites the instance copy at every start. It can add the editorial tools conditionally without forking, so the spec's "polite refusal" fallback is probably unnecessary.
13. **The "no hard-coded English" invariant already doesn't hold.** Tool errors ("I can't reach the site right now…") and `_humanize_age` ("yesterday") are English. They are LLM input rather than verbatim speech. Suggest scoping the invariant to new spoken confirmations *(amended in spec)*.
14. **Workspace root `CLAUDE.md` says "all our code goes in `reachy-demos/`".** Dreachy shipped as the standalone `dreachy-app/` (`reachy-demos/plans/dreachy.md` Task 6), so this plan targets `dreachy-app/`.

## Rulings (Vincenzo, 2026-09-30)

- **Repo:** R1 targets `dreachy-app/`. The workspace `Claude.md` and the
  `reachy-demos` routing table now say so, and `reachy-demos/src/dreachy/` is
  marked stale.
- **Spec amended** (see its Amendments section): the discovery route,
  `label`/`summary_field` in the mapping, empty types kept with a guessed
  body field, and the English-strings rule limited to spoken confirmations.
- **Search type list:** built from the discovered types at start. The
  settings page puts a "restart to apply" note beside the type checkboxes.
- **Git:** `docs/`, `specs/` and `plans/` are committed to `main` first, then
  the R1 branch is cut from `main`.
- **Execution:** single session. Vincenzo reviews the whole branch at the R1
  boundary.

### Carried into R2 planning

- The drupal-api-client OAuth gaps are fixed upstream in that package:
  a configurable refresh margin and one retry on 401. Dreachy doesn't build
  its own token handling.
- The secret-handling invariant becomes: "confined to the client/auth layer,
  never logged or in the profile". Amend the spec's Invariants when R2 is
  planned.

## Review Focus

Five inputs the spec doesn't mention that would bite a real installer, most likely first. Each is pinned by a test in the owning task.

1. **The robot boots before the network or site is up.** Startup discovery fails. Expected: the Umami fallback for now, then the real schema picked up without a restart once the site answers. Retries are throttled to one a minute. → Task 2, `test_a_failed_discovery_is_retried_once_the_retry_interval_has_passed`.
2. **The newest node of a type has an empty body (`null`).** The field must still be found from older samples. → Task 1, `test_a_field_empty_on_the_newest_node_is_still_found_on_older_ones`.
3. **The saved type selection names only types that no longer exist** (renamed on the site, or a different site URL). Expected: every discovered type, not none. The settings page doesn't send a selection from the old site when the URL or prefix is changed in the same save. → Task 1, `test_a_selection_of_only_vanished_types_falls_back_to_every_type`; Task 4 JS.
4. **The LLM passes a disabled or stale type.** The enum dates from start, so this can happen after a settings change. Expected: search widens to the enabled types, and a read by path of a disabled type finds nothing. → Task 2, `test_search_with_a_disabled_type_widens_to_every_enabled_type` and `test_reading_by_path_a_type_that_is_disabled_finds_nothing`.
5. **A type anonymous can't read, or one with no content yet.**
   - An unreadable type is skipped without sinking discovery.
   - An empty type is kept with guessed fields, so the watcher notices its first node.
   - → Task 1, `test_a_type_with_no_content_yet_is_kept_with_guessed_fields`; Task 2, `test_an_unreadable_type_is_skipped_without_sinking_discovery`.

Also pinned: no discovery requests go to the `https://example.com` placeholder before a site URL is saved (Task 3 and Task 4).

---

### Task 0: Branch and baseline

- [ ] **Step 1: Commit the spec, plan and docs to `main`, then branch**

```bash
git -C dreachy-app fetch origin
git -C dreachy-app status -sb   # expect: main level with origin/main; docs/ plans/ specs/ untracked
git -C dreachy-app add docs specs plans
git -C dreachy-app commit -m "docs: backend-and-auth spec, R1 plan, architecture notes"
git -C dreachy-app switch -c feat/r1-schema-driven-content-model
```

- [ ] **Step 2: Confirm the baseline**

Run: `cd dreachy-app && uv run pytest -q`
Expected: `44 passed`

---

### Task 1: Content-model heuristics (`schema.py`)

**Files:**
- Create: `dreachy/schema.py`
- Create: `tests/_fake_site.py` (fixture data only; Task 2 adds the mocked site)
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces:
  - `TypeSchema(label: str, label_field: str, text_fields: tuple[str, ...], summary_field: str | None = None)` (frozen dataclass)
  - `Schema = dict[str, TypeSchema]`
  - `LABEL_FIELD = "title"`
  - `FALLBACK_TYPES: Schema`
  - `humanize(machine_name: str) -> str`
  - `infer_type_schema(label: str, samples: Iterable[Mapping[str, Any]]) -> TypeSchema | None`
  - `guess_type_schema(bundle: str, label: str) -> TypeSchema`
  - `build_schema(bundles: Iterable[str], labels: Mapping[str, str], samples: Mapping[str, list[Mapping[str, Any]] | None]) -> Schema`
  - `fallback_schema(content_types: Iterable[str]) -> Schema`
  - `select_types(schema: Schema, enabled: Iterable[str]) -> Schema`
  - `tests/_fake_site.py`: `formatted(html) -> dict`, `node(bundle, uuid, title, created, **fields) -> dict`, `UMAMI_NODES`, `UMAMI_LABELS`, `NEWS_NODES`, `NEWS_LABELS`

- [ ] **Step 1: Write the fixture module**

`tests/_fake_site.py`:

```python
"""Fixture sites for content-model discovery tests.

Imported as a plain module (pytest's default import mode puts tests/ on
sys.path). Shaped like core JSON:API output: formatted text fields are
objects with value/format/processed; plain strings, numbers, lists and link
objects sit alongside them and must not be mistaken for text.
"""

from __future__ import annotations

from typing import Any


def formatted(html: str) -> dict[str, str]:
    """A formatted text field value as core JSON:API serialises it."""
    return {"value": html, "format": "basic_html", "processed": html}


def node(bundle: str, uuid: str, title: str, created: str, **fields: Any) -> dict[str, Any]:
    return {
        "type": f"node--{bundle}",
        "id": uuid,
        "attributes": {
            "drupal_internal__nid": 1,
            "langcode": "en",
            "status": True,
            "title": title,
            "created": created,
            "changed": created,
            "path": {"alias": f"/{bundle}/{uuid}", "pid": 1, "langcode": "en"},
            **fields,
        },
    }


# The Umami demo profile, as confirmed against the live site 2026-07-27.
UMAMI_LABELS = {"article": "Article", "page": "Basic page", "recipe": "Recipe"}
UMAMI_NODES = {
    "article": [
        node(
            "article", "a1", "Give your oatmeal the ultimate makeover", "2026-07-20T10:00:00+00:00",
            field_body=formatted("<p>Oatmeal is <strong>great</strong> for breakfast.</p>"),
        ),
    ],
    "page": [
        node(
            "page", "p1", "About Umami", "2026-07-15T08:00:00+00:00",
            field_body=formatted("<p>Umami is a fictional food magazine.</p>"),
        ),
    ],
    "recipe": [
        node(
            "recipe", "r1", "Borscht with pork ribs", "2026-07-25T12:00:00+00:00",
            field_cooking_time=60,
            field_difficulty="medium",
            field_ingredients=["1 kg pork ribs", "2 beetroots"],
            field_number_of_servings=6,
            field_preparation_time=20,
            field_recipe_instruction=formatted("<ol><li>Cook the ribs.</li></ol>"),
            field_summary=formatted("<p>A hearty Ukrainian soup.</p>"),
        ),
    ],
}

# An invented non-Umami model: core's standard body field (plus a second
# long-text field), a type whose teaser is its own field — listed first, so
# order alone can't pick the body — and a type with no text at all.
NEWS_LABELS = {"news_item": "News item", "event": "Event", "gallery": "Gallery"}
NEWS_NODES = {
    "news_item": [
        node(
            "news_item", "n2", "Council approves new park", "2026-09-28T09:00:00+00:00",
            body=formatted("<p>The council voted to build a park.</p>"),
            field_sidebar=formatted("<p>Related: parks map</p>"),
        ),
        node(
            "news_item", "n1", "Library reopens", "2026-09-20T09:00:00+00:00",
            body=formatted("<p>The library is open again.</p>"),
            field_sidebar=None,
        ),
    ],
    "event": [
        node(
            "event", "e1", "Harvest fair", "2026-09-25T09:00:00+00:00",
            field_teaser=formatted("<p>Fun for all ages.</p>"),
            field_description=formatted("<p>Stalls, music and a tractor parade.</p>"),
            field_date="2026-10-04",
        ),
    ],
    "gallery": [
        node("gallery", "g1", "Summer photos", "2026-08-01T09:00:00+00:00", field_photo_count=12),
    ],
}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_schema.py`:

```python
"""Unit tests for schema.py's content-model heuristics. Pure: no HTTP."""

from __future__ import annotations

from _fake_site import NEWS_LABELS, NEWS_NODES, UMAMI_LABELS, UMAMI_NODES, formatted

from dreachy.schema import (
    FALLBACK_TYPES,
    TypeSchema,
    build_schema,
    fallback_schema,
    humanize,
    infer_type_schema,
    select_types,
)


def _samples(nodes: dict) -> dict:
    return {bundle: [r["attributes"] for r in resources] for bundle, resources in nodes.items()}


def test_umami_mapping_falls_out_of_the_heuristics() -> None:
    assert build_schema(UMAMI_NODES, UMAMI_LABELS, _samples(UMAMI_NODES)) == FALLBACK_TYPES


def test_a_non_umami_model_needs_no_python_edits() -> None:
    assert build_schema(NEWS_NODES, NEWS_LABELS, _samples(NEWS_NODES)) == {
        "event": TypeSchema("Event", "title", ("field_description",), "field_teaser"),
        "news_item": TypeSchema("News item", "title", ("body", "field_sidebar")),
    }


def test_schema_is_ordered_by_machine_name() -> None:
    assert list(build_schema(NEWS_NODES, NEWS_LABELS, _samples(NEWS_NODES))) == ["event", "news_item"]


def test_body_is_preferred_over_field_body_and_other_long_text() -> None:
    attributes = {"field_notes": formatted("n"), "field_body": formatted("fb"), "body": formatted("b")}

    assert infer_type_schema("X", [attributes]).text_fields == ("body", "field_body", "field_notes")


def test_a_field_empty_on_the_newest_node_is_still_found_on_older_ones() -> None:
    newest = {"title": "t", "body": None}
    older = {"title": "t", "body": formatted("<p>text</p>")}

    assert infer_type_schema("X", [newest, older]).text_fields == ("body",)


def test_a_type_with_no_formatted_text_is_skipped() -> None:
    assert infer_type_schema("Gallery", [{"title": "t", "field_photo_count": 12, "field_tags": ["a"]}]) is None


def test_plain_strings_and_link_objects_are_not_text() -> None:
    attributes = {
        "field_subtitle": "plain",
        "path": {"alias": "/x", "pid": 1, "langcode": "en"},
        "field_link": {"uri": "https://example.com", "title": "x"},
    }

    assert infer_type_schema("X", [attributes]) is None


def test_a_lone_teaser_named_field_is_used_as_the_body() -> None:
    assert infer_type_schema("X", [{"field_summary": formatted("s")}]) == TypeSchema("X", "title", ("field_summary",))


def test_a_type_with_no_content_yet_is_kept_with_guessed_fields() -> None:
    schema = build_schema(["news_item", "recipe"], {}, {"news_item": [], "recipe": []})

    assert schema["news_item"] == TypeSchema("News item", "title", ("body", "field_body"))
    # An Umami type keeps its known mapping, plus the common names in case this isn't Umami.
    assert schema["recipe"] == TypeSchema(
        "Recipe", "title", ("field_recipe_instruction", "body", "field_body"), "field_summary"
    )


def test_a_type_that_could_not_be_read_is_skipped() -> None:
    samples = {"article": _samples(UMAMI_NODES)["article"], "secret": None}

    assert list(build_schema(["article", "secret"], {}, samples)) == ["article"]


def test_missing_labels_are_derived_from_the_machine_name() -> None:
    assert humanize("news_item") == "News item"
    assert build_schema(NEWS_NODES, {}, _samples(NEWS_NODES))["news_item"].label == "News item"


def test_fallback_schema_is_the_pre_discovery_umami_table() -> None:
    assert fallback_schema(("article", "page", "recipe")) == FALLBACK_TYPES


def test_an_empty_selection_means_every_type() -> None:
    assert select_types(FALLBACK_TYPES, ()) == FALLBACK_TYPES


def test_a_selection_keeps_schema_order() -> None:
    assert list(select_types(FALLBACK_TYPES, ("recipe", "article"))) == ["article", "recipe"]


def test_a_selection_of_only_vanished_types_falls_back_to_every_type() -> None:
    assert select_types(FALLBACK_TYPES, ("news_item",)) == FALLBACK_TYPES
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_schema.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'dreachy.schema'`

- [ ] **Step 4: Implement `dreachy/schema.py`**

```python
"""Content-model discovery: which node types a site has, and which of their
fields hold speakable text.

Pure functions over JSON:API data, no HTTP: DrupalClient.get_schema()
fetches the evidence, this module decides.

Discovery route (chosen 2026-09-30 against Drupal core 11.4.4):

- Bundle list: the JSON:API index (``/jsonapi``). Always readable
  anonymously, and lists exactly the resource types JSON:API exposes.
- Human labels: ``node_type--node_type``, best effort. Core 11.4 lets anyone
  with ``access content`` view node types; older cores and locked-down sites
  don't, so a missing label is derived from the machine name.
- Text fields: inferred from sample nodes. Field definitions
  (``field_config--field_config``) need ``administer node fields``, which
  anonymous never has, so the shape of real attribute values is the only
  anonymous signal: formatted text is an object with ``value`` plus
  ``format`` or ``processed``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Every node type's label field is its title; kept per type so the client
# never hard-codes it.
LABEL_FIELD = "title"

_BODY_PREFERENCE = ("body", "field_body")
_SUMMARY_HINTS = ("summary", "teaser")


@dataclass(frozen=True)
class TypeSchema:
    label: str
    label_field: str
    # Body candidates, best first: the first non-empty one is read aloud.
    text_fields: tuple[str, ...]
    # A teaser that's a field of its own (Umami's recipe), if the type has one.
    summary_field: str | None = None


Schema = dict[str, TypeSchema]

# The Umami demo profile, confirmed against the live site 2026-07-27. Used
# when discovery fails outright, and as the best guess for an Umami type
# with no content yet to infer from.
FALLBACK_TYPES: Schema = {
    "article": TypeSchema("Article", LABEL_FIELD, ("field_body",)),
    "page": TypeSchema("Basic page", LABEL_FIELD, ("field_body",)),
    "recipe": TypeSchema("Recipe", LABEL_FIELD, ("field_recipe_instruction",), "field_summary"),
}


def humanize(machine_name: str) -> str:
    """``news_item`` -> ``News item``: a label for a type the site didn't name."""
    return machine_name.replace("_", " ").capitalize()


def _is_formatted_text(value: Any) -> bool:
    return isinstance(value, dict) and "value" in value and ("format" in value or "processed" in value)


def infer_type_schema(label: str, samples: Iterable[Mapping[str, Any]]) -> TypeSchema | None:
    """Pick a type's text fields from sample nodes' attributes.

    Samples are unioned, so a field left empty on the newest node is still
    found on an older one. Returns None for a type with no formatted text.
    """
    candidates: list[str] = []
    for attributes in samples:
        for name, value in attributes.items():
            if name not in candidates and _is_formatted_text(value):
                candidates.append(name)

    summary = next((name for name in candidates if any(hint in name for hint in _SUMMARY_HINTS)), None)
    body = [name for name in candidates if name != summary]
    if not body:
        if summary is None:
            return None
        # A lone teaser-named field is still the only text the type has.
        body, summary = [summary], None
    preferred = [name for name in _BODY_PREFERENCE if name in body]
    rest = [name for name in body if name not in preferred]
    return TypeSchema(label, LABEL_FIELD, tuple(preferred + rest), summary)


def guess_type_schema(bundle: str, label: str) -> TypeSchema:
    """A type with nothing to infer from: its Umami mapping if it has one,
    plus the common body field names either way."""
    known = FALLBACK_TYPES.get(bundle)
    known_fields = known.text_fields if known else ()
    fields = tuple(dict.fromkeys((*known_fields, *_BODY_PREFERENCE)))
    return TypeSchema(label, LABEL_FIELD, fields, known.summary_field if known else None)


def build_schema(
    bundles: Iterable[str],
    labels: Mapping[str, str],
    samples: Mapping[str, list[Mapping[str, Any]] | None],
) -> Schema:
    """Assemble the site's schema, ordered by machine name.

    ``samples[bundle]`` is None when the bundle couldn't be read (skipped),
    and empty when it has no content yet (kept with guessed fields, so the
    watcher still notices its first node).
    """
    schema: Schema = {}
    for bundle in sorted(bundles):
        bundle_samples = samples.get(bundle)
        if bundle_samples is None:
            continue
        label = labels.get(bundle) or humanize(bundle)
        if bundle_samples:
            type_schema = infer_type_schema(label, bundle_samples)
        else:
            type_schema = guess_type_schema(bundle, label)
        if type_schema is not None:
            schema[bundle] = type_schema
    return schema


def fallback_schema(content_types: Iterable[str]) -> Schema:
    """The pre-discovery mapping, for when the site can't be read."""
    return {t: FALLBACK_TYPES.get(t) or guess_type_schema(t, humanize(t)) for t in content_types}


def select_types(schema: Schema, enabled: Iterable[str]) -> Schema:
    """The installer's enabled subset of *schema*, in schema order.

    Empty *enabled* means every type. So does a selection naming only types
    the site no longer has: a stale setting mustn't leave Dreachy knowing
    nothing.
    """
    wanted = set(enabled)
    if not wanted:
        return dict(schema)
    selected = {t: s for t, s in schema.items() if t in wanted}
    if not selected:
        logger.warning("None of the enabled content types %s exist on the site; using all of them", sorted(wanted))
        return dict(schema)
    return selected
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_schema.py -q`
Expected: `15 passed`

- [ ] **Step 6: Commit**

```bash
git add dreachy/schema.py tests/_fake_site.py tests/test_schema.py
git commit -m "feat: content-model heuristics for schema discovery

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Schema discovery and schema-driven queries in `DrupalClient`

**Files:**
- Modify: `dreachy/config.py` (content-model section, lines 24–27)
- Modify: `dreachy/client.py` (whole file; full replacement below)
- Modify: `tests/_fake_site.py` (append `FakeSite`)
- Test: `tests/test_discovery.py`

**Interfaces:**
- Consumes (from Task 1): `Schema`, `TypeSchema`, `build_schema`, `fallback_schema`, `select_types`
- Produces:
  - `Config.enabled_types: tuple[str, ...] = ()`
  - `Config.schema_sample_size: int = 5`
  - `Config.schema_retry_seconds: float = 60.0`
  - `DrupalClient(config, *, http_client=None, auto_discover: bool = False)`
  - `DrupalClient.get_schema() -> Schema`: HTTP; raises `DreachySiteError`
  - `DrupalClient.refresh_schema() -> bool`: never raises `DreachySiteError`
  - `DrupalClient.schema -> Schema`: property; enabled types only; no I/O
  - `DrupalClient.full_schema -> Schema`: property; all known types; no I/O
  - `DrupalClient.schema_discovered: bool`
  - `tests/_fake_site.FakeSite(nodes, *, labels=None, locale="en", index_status=200, node_types_status=200, unreadable=())`, with `.client(config=None, **kwargs) -> DrupalClient`, `.requests: list[str]`, `.index_status` (mutable)

- [ ] **Step 1: Append the mocked site to `tests/_fake_site.py`**

Add these imports to the top of the file, after `from typing import Any`:

```python
import re

import httpx

from dreachy.client import DrupalClient
from dreachy.config import Config
```

Append at the end:

```python
def _error(status: int) -> httpx.Response:
    return httpx.Response(status, json={"errors": [{"status": str(status)}]})


class FakeSite:
    """A mocked Drupal JSON:API site for discovery tests.

    Serves the JSON:API index, node types, node collections (sorted, limited
    and title-filtered like the real thing), single nodes and Decoupled
    Router path lookups. Every request path is recorded in ``requests``.
    """

    def __init__(
        self,
        nodes: dict[str, list[dict[str, Any]]],
        *,
        labels: dict[str, str] | None = None,
        locale: str | None = "en",
        index_status: int = 200,
        node_types_status: int = 200,
        unreadable: tuple[str, ...] = (),
    ) -> None:
        self.nodes = nodes
        self.labels = labels or {}
        self.locale = locale
        self.index_status = index_status
        self.node_types_status = node_types_status
        self.unreadable = unreadable
        self.requests: list[str] = []
        prefix = f"/{locale}" if locale else ""
        self._api = f"{prefix}/jsonapi"
        self._router = f"{prefix}/router/translate-path"

    def client(self, config: Config | None = None, **kwargs: Any) -> DrupalClient:
        config = config or Config()
        config.default_locale = self.locale
        transport = httpx.MockTransport(self.handle)
        return DrupalClient(config, http_client=httpx.Client(transport=transport), **kwargs)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        if path == self._api:
            return self._index()
        if path == f"{self._api}/node_type/node_type":
            return self._node_types()
        if match := re.fullmatch(rf"{re.escape(self._api)}/node/(\w+)/([\w-]+)", path):
            return self._resource(*match.groups())
        if match := re.fullmatch(rf"{re.escape(self._api)}/node/(\w+)", path):
            return self._collection(match.group(1), request.url.params)
        if path == self._router:
            return self._resolve(request.url.params.get("path"))
        return _error(404)

    def _index(self) -> httpx.Response:
        if self.index_status != 200:
            return _error(self.index_status)
        base = f"https://example.com{self._api}"
        links: dict[str, Any] = {
            "self": {"href": base},
            "node_type--node_type": {"href": f"{base}/node_type/node_type"},
        }
        for bundle in self.nodes:
            links[f"node--{bundle}"] = {"href": f"{base}/node/{bundle}"}
        return httpx.Response(200, json={"jsonapi": {"version": "1.1"}, "data": [], "links": links})

    def _node_types(self) -> httpx.Response:
        if self.node_types_status != 200:
            return _error(self.node_types_status)
        data = [
            {
                "type": "node_type--node_type",
                "id": f"{bundle}-type-uuid",
                "attributes": {"drupal_internal__type": bundle, "name": self.labels.get(bundle, bundle)},
            }
            for bundle in self.nodes
        ]
        return httpx.Response(200, json={"data": data, "links": {}})

    def _collection(self, bundle: str, params: httpx.QueryParams) -> httpx.Response:
        if bundle in self.unreadable:
            return _error(403)
        if bundle not in self.nodes:
            return _error(404)
        data = sorted(self.nodes[bundle], key=lambda r: r["attributes"]["created"], reverse=True)
        if contains := params.get("filter[title][value]"):
            data = [r for r in data if contains.lower() in r["attributes"]["title"].lower()]
        elif equal := params.get("filter[title]"):
            data = [r for r in data if r["attributes"]["title"] == equal]
        if limit := params.get("page[limit]"):
            data = data[: int(limit)]
        return httpx.Response(200, json={"data": data, "links": {}})

    def _resource(self, bundle: str, uuid: str) -> httpx.Response:
        for resource in self.nodes.get(bundle, []):
            if resource["id"] == uuid:
                return httpx.Response(200, json={"data": resource})
        return _error(404)

    def _resolve(self, alias: str | None) -> httpx.Response:
        for bundle, resources in self.nodes.items():
            for resource in resources:
                if resource["attributes"]["path"]["alias"] == alias:
                    entity = {"type": "node", "bundle": bundle, "uuid": resource["id"]}
                    return httpx.Response(200, json={"resolved": True, "isHomePath": False, "entity": entity})
        return httpx.Response(200, json={"resolved": False, "message": f"Unable to resolve {alias!r}."})
```

- [ ] **Step 2: Write the failing tests**

`tests/test_discovery.py`:

```python
"""Mocked-HTTP tests for DrupalClient's schema discovery and the schema-driven
queries built on it. No live site: see _fake_site.FakeSite."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from _fake_site import NEWS_LABELS, NEWS_NODES, UMAMI_LABELS, UMAMI_NODES, FakeSite

import dreachy.client as client_module
from dreachy.client import DreachySiteError, DrupalClient
from dreachy.config import Config
from dreachy.schema import FALLBACK_TYPES

# ---------------------------------------------------------------------------
# get_schema / refresh_schema
# ---------------------------------------------------------------------------


def test_get_schema_discovers_umami_as_the_fallback_table() -> None:
    with FakeSite(UMAMI_NODES, labels=UMAMI_LABELS).client() as client:
        assert client.get_schema() == FALLBACK_TYPES


def test_get_schema_reads_the_unprefixed_index_on_a_single_language_site() -> None:
    site = FakeSite(NEWS_NODES, labels=NEWS_LABELS, locale=None)

    with site.client() as client:
        assert list(client.get_schema()) == ["event", "news_item"]
    assert site.requests[0] == "/jsonapi"


def test_labels_fall_back_to_machine_names_when_node_types_are_forbidden() -> None:
    with FakeSite(NEWS_NODES, node_types_status=403).client() as client:
        assert client.get_schema()["news_item"].label == "News item"


def test_an_unreadable_type_is_skipped_without_sinking_discovery() -> None:
    with FakeSite(UMAMI_NODES, labels=UMAMI_LABELS, unreadable=("page",)).client() as client:
        assert list(client.get_schema()) == ["article", "recipe"]


def test_get_schema_raises_site_error_when_the_index_is_unreachable() -> None:
    with FakeSite(UMAMI_NODES, index_status=500).client() as client:
        with pytest.raises(DreachySiteError):
            client.get_schema()


def test_get_schema_raises_site_error_when_the_site_is_not_drupal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not a Drupal site</html>")

    client = DrupalClient(Config(), http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(DreachySiteError):
        client.get_schema()


def test_refresh_schema_keeps_the_fallback_when_the_site_is_down() -> None:
    with FakeSite(UMAMI_NODES, index_status=503).client() as client:
        assert client.refresh_schema() is False
        assert client.schema_discovered is False
        assert client.full_schema == FALLBACK_TYPES


def test_refresh_schema_replaces_the_fallback_with_the_sites_types() -> None:
    with FakeSite(NEWS_NODES, labels=NEWS_LABELS).client() as client:
        assert client.refresh_schema() is True
        assert client.schema_discovered is True
        assert list(client.schema) == ["event", "news_item"]


# ---------------------------------------------------------------------------
# The spec's goal: a news_item/body site works without editing Python
# ---------------------------------------------------------------------------


def test_a_news_item_site_works_end_to_end_without_python_edits() -> None:
    with FakeSite(NEWS_NODES, labels=NEWS_LABELS).client(auto_discover=True) as client:
        nodes = client.get_recent_nodes(limit=10)
        story = client.get_article("Council approves new park")
        [event] = client.find_content("harvest")

    assert [n["type"] for n in nodes] == ["news_item", "event", "news_item"]
    assert story["body"] == "The council voted to build a park."
    assert event["body"] == "Stalls, music and a tractor parade."
    assert event["summary"] == "Fun for all ages."


# ---------------------------------------------------------------------------
# When discovery happens
# ---------------------------------------------------------------------------


def test_a_client_without_auto_discover_never_asks_for_the_schema() -> None:
    site = FakeSite(NEWS_NODES)

    with site.client(Config(content_types=("news_item",))) as client:
        client.get_recent_nodes()

    assert site.requests == ["/en/jsonapi/node/news_item"]


def test_a_failed_discovery_is_retried_once_the_retry_interval_has_passed(monkeypatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(client_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    # Index down, content up: the fallback Umami types still answer meanwhile.
    site = FakeSite(UMAMI_NODES, labels=UMAMI_LABELS, index_status=503)

    with site.client(Config(schema_retry_seconds=60), auto_discover=True) as client:
        client.get_recent_nodes()
        assert site.requests.count("/en/jsonapi") == 1

        clock[0] += 30
        client.get_recent_nodes()
        assert site.requests.count("/en/jsonapi") == 1  # throttled

        site.index_status = 200
        clock[0] += 31
        client.get_recent_nodes()
        assert site.requests.count("/en/jsonapi") == 2
        assert client.schema_discovered is True


# ---------------------------------------------------------------------------
# The installer's type selection
# ---------------------------------------------------------------------------


def test_enabled_types_limit_what_dreachy_queries() -> None:
    site = FakeSite(UMAMI_NODES, labels=UMAMI_LABELS)

    with site.client(Config(enabled_types=("recipe",))) as client:
        nodes = client.get_recent_nodes(limit=10)

    assert [n["type"] for n in nodes] == ["recipe"]
    assert site.requests == ["/en/jsonapi/node/recipe"]


def test_search_with_a_disabled_type_widens_to_every_enabled_type() -> None:
    site = FakeSite(UMAMI_NODES, labels=UMAMI_LABELS)

    with site.client(Config(enabled_types=("article", "recipe"))) as client:
        matches = client.find_content("borscht", content_type="page")

    assert [m["id"] for m in matches] == ["r1"]
    assert "/en/jsonapi/node/page" not in site.requests


def test_reading_by_path_a_type_that_is_disabled_finds_nothing() -> None:
    with FakeSite(UMAMI_NODES, labels=UMAMI_LABELS).client(Config(enabled_types=("recipe",))) as client:
        assert client.get_article("/article/a1") is None
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_discovery.py -q`
Expected: FAIL. Several tests fail with `TypeError: ... unexpected keyword argument 'auto_discover'`, or `AttributeError: 'DrupalClient' object has no attribute 'get_schema'`.

- [ ] **Step 4: Extend `Config`**

In `dreachy/config.py`, replace the content-model section:

```python
    # ---------------------------------------------------------------------------
    # Content model (Umami demo profile — confirmed 2026-07-27)
    # ---------------------------------------------------------------------------
    content_types: tuple[str, ...] = ("article", "page", "recipe")
```

with:

```python
    # ---------------------------------------------------------------------------
    # Content model — discovered from the site (see schema.py)
    # ---------------------------------------------------------------------------
    # Fallback only: the types queried until a discovery succeeds (Umami demo
    # profile — confirmed 2026-07-27).
    content_types: tuple[str, ...] = ("article", "page", "recipe")
    # The installer's selection from the settings page (DREACHY_TYPES).
    # Empty means every type the site has, including ones added later.
    enabled_types: tuple[str, ...] = ()
    # Discovery reads this many recent nodes per type to find its text
    # fields, so a field left empty on one node doesn't hide it.
    schema_sample_size: int = 5
    # After a failed discovery, queries retry it at most this often.
    schema_retry_seconds: float = 60.0
```

- [ ] **Step 5: Replace `dreachy/client.py`**

```python
"""DrupalClient — thin sync wrapper over drupal-api-client's JsonApiClient.

Returns plain dicts only; no drupal_api_client types leak past this module.
Sync-only (per drupal-api-client) — tools call this via asyncio.to_thread().

Which node types to query, and which of their fields hold the text, comes
from the site itself: get_schema() discovers it (schema.py documents the
route and heuristics). Until a discovery succeeds the client uses
schema.FALLBACK_TYPES, the Umami mapping confirmed against the live demo
site 2026-07-27. Text fields render as HTML (even the "processed"
variant), so every text extraction goes through ``_strip_html``.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from types import TracebackType
from typing import Any
from urllib.parse import urljoin

import httpx
from drupal_api_client import JsonApiClient, ResourceNotFoundError
from drupal_jsonapi_params import DrupalJsonApiParams
from drupal_jsonapi_params.operators import FilterOperator

from .config import Config
from .schema import Schema, TypeSchema, build_schema, fallback_schema, select_types

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_SUMMARY_FALLBACK_CHARS = 200


class DreachySiteError(Exception):
    """The Drupal site is unreachable or returned an error response."""


def _strip_html(text: str) -> str:
    """Collapse Drupal's rendered HTML into plain, speakable text."""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text)).strip()


def _text_field(attributes: dict[str, Any], field_name: str | None) -> str:
    if not field_name:
        return ""
    field = attributes.get(field_name)
    if not isinstance(field, dict):
        return ""
    return _strip_html(field.get("processed") or field.get("value") or "")


def _node_to_dict(bundle: str, type_schema: TypeSchema, resource: dict[str, Any]) -> dict[str, Any]:
    attributes = resource["attributes"]
    body = next((text for name in type_schema.text_fields if (text := _text_field(attributes, name))), "")
    summary = _text_field(attributes, type_schema.summary_field) or body[:_SUMMARY_FALLBACK_CHARS]
    path = attributes.get("path") or {}
    return {
        "id": resource["id"],
        "title": attributes.get(type_schema.label_field),
        "type": bundle,
        "created": attributes.get("created"),
        "changed": attributes.get("changed"),
        "path": path.get("alias"),
        "body": body,
        "summary": summary,
    }


class DrupalClient:
    """Sync wrapper over JsonApiClient exposing Dreachy's query behaviours."""

    def __init__(
        self, config: Config, *, http_client: httpx.Client | None = None, auto_discover: bool = False
    ) -> None:
        self.config = config
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
        # auto_discover: queries retry a failed discovery (see _types). Off
        # by default, so a client built for a test, or before a site URL is
        # configured, never sends discovery requests of its own accord.
        self._auto_discover = auto_discover
        self._full_schema: Schema = fallback_schema(config.content_types)
        self.schema_discovered = False
        self._next_discovery_at = 0.0
        self._discovery_lock = threading.Lock()

    def __enter__(self) -> DrupalClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _get_collection(self, resource_type: str, params: DrupalJsonApiParams) -> list[dict[str, Any]]:
        try:
            response = self._client.get_collection(
                resource_type, query_string=params, raise_for_status=True, disable_cache=True
            )
        except httpx.HTTPError as exc:
            raise DreachySiteError(str(exc)) from exc
        return response["data"]

    # -- content model --------------------------------------------------

    @property
    def full_schema(self) -> Schema:
        """Every type Dreachy knows of, before the installer's selection."""
        return dict(self._full_schema)

    @property
    def schema(self) -> Schema:
        """The types Dreachy talks about. Never touches the network."""
        return select_types(self._full_schema, self.config.enabled_types)

    def get_schema(self) -> Schema:
        """Discover the site's node types and their text fields.

        Raises DreachySiteError when the JSON:API index can't be read.
        """
        bundles = self._node_bundles()
        labels = self._node_type_labels()
        samples = {bundle: self._sample_attributes(bundle) for bundle in bundles}
        return build_schema(bundles, labels, samples)

    def refresh_schema(self) -> bool:
        """Replace the cached schema with a fresh discovery.

        On failure keeps the current schema (the fallback, or the last one
        discovered) and returns False; never raises DreachySiteError.
        """
        with self._discovery_lock:
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

    def _types(self) -> Schema:
        """The enabled schema, first retrying a failed discovery if one is due."""
        if self._auto_discover and not self.schema_discovered and time.monotonic() >= self._next_discovery_at:
            self.refresh_schema()
        return self.schema

    def _index_url(self) -> str:
        locale = self.config.default_locale
        return urljoin(self._client.base_url, f"{locale}/jsonapi" if locale else "jsonapi")

    def _node_bundles(self) -> list[str]:
        try:
            response = self._client.fetch(self._index_url(), raise_for_status=True)
            links = response.json().get("links", {})
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            raise DreachySiteError(f"JSON:API index unavailable: {exc}") from exc
        return [key.split("--", 1)[1] for key in links if key.startswith("node--")]

    def _node_type_labels(self) -> dict[str, str]:
        """Human labels, best effort: anonymous may not be allowed to view node types."""
        try:
            resources = self._get_collection("node_type--node_type", DrupalJsonApiParams())
        except DreachySiteError as exc:
            logger.info("Node type labels unavailable, using machine names: %s", exc)
            return {}
        labels: dict[str, str] = {}
        for resource in resources:
            attributes = resource.get("attributes") or {}
            if attributes.get("drupal_internal__type") and attributes.get("name"):
                labels[attributes["drupal_internal__type"]] = attributes["name"]
        return labels

    def _sample_attributes(self, bundle: str) -> list[dict[str, Any]] | None:
        """Recent nodes' attributes, or None if this type can't be read at all."""
        params = DrupalJsonApiParams().add_sort("created", "DESC").add_page_limit(self.config.schema_sample_size)
        try:
            resources = self._get_collection(f"node--{bundle}", params)
        except DreachySiteError as exc:
            # One unreadable type (e.g. no anonymous view access) mustn't sink the rest.
            logger.info("Can't sample node--%s, skipping it: %s", bundle, exc)
            return None
        return [resource.get("attributes") or {} for resource in resources]

    # -- queries --------------------------------------------------------

    def get_recent_nodes(self, limit: int | None = None) -> list[dict[str, Any]]:
        limit = limit or self.config.whats_new_limit
        nodes: list[dict[str, Any]] = []
        for bundle, type_schema in self._types().items():
            params = DrupalJsonApiParams().add_sort("created", "DESC").add_page_limit(limit)
            for resource in self._get_collection(f"node--{bundle}", params):
                nodes.append(_node_to_dict(bundle, type_schema, resource))
        nodes.sort(key=lambda n: n["created"], reverse=True)
        return nodes[:limit]

    def find_content(
        self, keyword: str, content_type: str | None = None
    ) -> list[dict[str, Any]]:
        limit = self.config.find_content_limit
        types = self._types()
        # An unknown or disabled type (e.g. from a tool spec built before a
        # settings change) searches every enabled type rather than finding
        # nothing.
        if content_type in types:
            types = {content_type: types[content_type]}
        matches: list[dict[str, Any]] = []
        for bundle, type_schema in types.items():
            params = (
                DrupalJsonApiParams()
                .add_filter(type_schema.label_field, keyword, operator=FilterOperator.CONTAINS)
                .add_sort("created", "DESC")
                .add_page_limit(limit)
            )
            for resource in self._get_collection(f"node--{bundle}", params):
                matches.append(_node_to_dict(bundle, type_schema, resource))
        matches.sort(key=lambda n: n["created"], reverse=True)
        return matches[:limit]

    def get_article(self, title_or_path: str) -> dict[str, Any] | None:
        types = self._types()
        path = title_or_path if title_or_path.startswith("/") else f"/{title_or_path}"
        resource = None
        try:
            resource = self._client.get_resource_by_path(path, raise_for_status=True, disable_cache=True)
        except ResourceNotFoundError:
            pass
        except httpx.HTTPError as exc:
            raise DreachySiteError(str(exc)) from exc

        if resource is not None:
            bundle = resource["data"]["type"].split("--")[1]
            if bundle in types:
                return _node_to_dict(bundle, types[bundle], resource["data"])
            # A path to something Dreachy doesn't talk about (a disabled
            # type, a taxonomy term): fall through to the title search.

        for bundle, type_schema in types.items():
            params = (
                DrupalJsonApiParams()
                .add_filter(type_schema.label_field, title_or_path, operator=FilterOperator.EQUAL)
                .add_page_limit(1)
            )
            found = self._get_collection(f"node--{bundle}", params)
            if found:
                return _node_to_dict(bundle, type_schema, found[0])
        return None

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

- [ ] **Step 6: Run the new and existing tests**

Run: `uv run pytest -q`
Expected: all pass: `44 + 15 + 14 = 73 passed`. The existing `test_client.py`, `test_queries.py` and `test_drupal_watch_site.py` pass unchanged. They build clients without `auto_discover`, so they run on the fallback table exactly as before.

- [ ] **Step 7: Commit**

```bash
git add dreachy/config.py dreachy/client.py tests/_fake_site.py tests/test_discovery.py
git commit -m "feat: discover the site's content types and drive queries from them

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Wire discovery into start-up (`Config.from_env`, `_shared`, `main.py`)

**Files:**
- Modify: `dreachy/config.py` (add `import os`, `Config.from_env`, `parse_types`)
- Modify: `dreachy/tools/_shared.py:17-39`
- Modify: `dreachy/main.py` (imports; new `_load_instance_env`, `_warm_schema`; `Dreachy.run`)
- Test: `tests/test_settings_routes.py` (fixture and new tests)

**Interfaces:**
- Consumes (from Task 2): `DrupalClient(..., auto_discover=...)`, `DrupalClient.refresh_schema()`
- Produces:
  - `Config.from_env() -> Config` (reads `DREACHY_BASE_URL`, `DREACHY_LOCALE`, `DREACHY_TYPES`)
  - `parse_types(value: str) -> tuple[str, ...]`
  - `dreachy.main._load_instance_env() -> None`
  - `dreachy.main._warm_schema() -> None`
  - `dreachy.main.get_client` (imported name, patchable in tests)

- [ ] **Step 1: Tighten the settings-test fixture and write the failing tests**

In `tests/test_settings_routes.py`, replace the two `monkeypatch.delenv(...)` lines in `_isolated_paths` with the loop below. `setenv` then `delenv` makes monkeypatch restore the key's original absence on teardown, so values the routes write into `os.environ` don't leak into later test files:

```python
    for key in ("DREACHY_BASE_URL", "DREACHY_LOCALE", "DREACHY_TYPES"):
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
```

Append these tests:

```python
# ---------------------------------------------------------------------------
# Start-up: the instance .env is loaded and the schema warmed before the
# conversation app builds its tool specs.
# ---------------------------------------------------------------------------


class _RecordingClient:
    def __init__(self) -> None:
        self.refreshes = 0

    def refresh_schema(self) -> bool:
        self.refreshes += 1
        return True


def test_load_instance_env_puts_saved_settings_into_the_environment() -> None:
    (dreachy_main._instance_path() / ".env").write_text(
        "DREACHY_BASE_URL=https://saved.example\nDREACHY_TYPES=recipe\n"
    )

    dreachy_main._load_instance_env()

    assert os.environ["DREACHY_BASE_URL"] == "https://saved.example"
    assert os.environ["DREACHY_TYPES"] == "recipe"


def test_load_instance_env_is_a_no_op_on_a_fresh_install() -> None:
    dreachy_main._load_instance_env()

    assert "DREACHY_BASE_URL" not in os.environ


def test_warm_schema_discovers_once_a_site_url_is_set(monkeypatch) -> None:
    recorder = _RecordingClient()
    monkeypatch.setattr(dreachy_main, "get_client", lambda: recorder)
    monkeypatch.setenv("DREACHY_BASE_URL", "https://site.example")

    dreachy_main._warm_schema()

    assert recorder.refreshes == 1


def test_warm_schema_sends_nothing_before_a_site_url_is_set(monkeypatch) -> None:
    recorder = _RecordingClient()
    monkeypatch.setattr(dreachy_main, "get_client", lambda: recorder)

    dreachy_main._warm_schema()

    assert recorder.refreshes == 0


def test_warm_schema_survives_an_unexpected_discovery_error(monkeypatch) -> None:
    class _Exploding:
        def refresh_schema(self) -> bool:
            raise KeyError("attributes")

    monkeypatch.setattr(dreachy_main, "get_client", lambda: _Exploding())
    monkeypatch.setenv("DREACHY_BASE_URL", "https://site.example")

    dreachy_main._warm_schema()  # must not raise: start-up survives a misbehaving site


def test_get_client_reads_the_type_selection(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_TYPES", "recipe, article,")

    assert shared.get_client().config.enabled_types == ("recipe", "article")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_settings_routes.py -q`
Expected: the new tests FAIL with `AttributeError: module 'dreachy.main' has no attribute '_load_instance_env'` (and similar). The 13 existing tests still pass.

- [ ] **Step 3: Add `Config.from_env` and `parse_types`**

In `dreachy/config.py`, add `import os` below `from dataclasses import dataclass`. At the end of the `Config` class body, after `watch_max_backoff_seconds`, add:

```python

    @classmethod
    def from_env(cls) -> "Config":
        """A Config with the installer's settings applied (instance .env / settings page)."""
        config = cls()
        if base_url := os.environ.get("DREACHY_BASE_URL"):
            config.base_url = base_url
        # Deliberately not `if locale :=` — an empty DREACHY_LOCALE is a real
        # setting ("this site has no language prefix"), not an absent one.
        locale = os.environ.get("DREACHY_LOCALE")
        if locale is not None:
            config.default_locale = locale or None
        config.enabled_types = parse_types(os.environ.get("DREACHY_TYPES", ""))
        return config


def parse_types(value: str) -> tuple[str, ...]:
    """``"recipe, article,"`` -> ``("recipe", "article")``."""
    return tuple(part.strip() for part in value.split(",") if part.strip())
```

- [ ] **Step 4: Build the shared client from `Config.from_env`**

Replace `get_client` and `reset_client` in `dreachy/tools/_shared.py` (and drop the now-unused `from dreachy.config import Config` import only if nothing else uses it; `Config.from_env` still needs it, so keep it):

```python
def get_client() -> DrupalClient:
    global _client
    if _client is None:
        # Discovery only once a real site is configured: the placeholder
        # base_url would send it to example.com.
        _client = DrupalClient(Config.from_env(), auto_discover=bool(os.environ.get("DREACHY_BASE_URL")))
    return _client


def reset_client() -> None:
    """Drop the cached client, and with it the cached schema.

    Called whenever the settings page saves, so a new site URL, language
    prefix or type selection takes effect immediately — no app restart
    needed — and the next use rediscovers the site's content types.
    """
    global _client
    _client = None
```

- [ ] **Step 5: Load the `.env` and warm the schema at start in `main.py`**

Change the imports:

```python
import logging
import os
import threading
```

```python
from dreachy.config import Config
from dreachy.tools._shared import get_client, reset_client

logger = logging.getLogger(__name__)
```

Add after `_configure_environment()`:

```python
def _load_instance_env() -> None:
    """Load the instance .env into os.environ before anything reads it.

    The conversation app loads this same file too, but only once its audio
    stream launches — after it has built its tool specs. Dreachy needs the
    site settings earlier, to discover the site's content types before
    those specs are built (so drupal_find_content lists the site's own).
    """
    env_path = _instance_path() / ".env"
    if env_path.exists():
        dotenv.load_dotenv(env_path, override=True)


def _warm_schema() -> None:
    """Discover the site's content model once at start.

    Skipped until a site URL is configured: the placeholder would send
    discovery requests to example.com. A failure leaves the Umami fallback
    in place, and the client retries discovery as it's used.
    """
    if not os.environ.get("DREACHY_BASE_URL"):
        return
    try:
        get_client().refresh_schema()
    except Exception:
        logger.exception("Content-type discovery failed at start; continuing with the fallback")
```

In `Dreachy.run`, replace the first two lines:

```python
        _render_profile()
        _configure_environment()
```

with:

```python
        _load_instance_env()
        _render_profile()
        _configure_environment()
        _warm_schema()
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest -q`
Expected: `79 passed`

- [ ] **Step 7: Commit**

```bash
git add dreachy/config.py dreachy/tools/_shared.py dreachy/main.py tests/test_settings_routes.py
git commit -m "feat: load instance settings and discover content types at start

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Settings page — type selection and rediscovery on save

**Files:**
- Modify: `dreachy/main.py` (`_ConfigPayload`, `_register_settings_routes`)
- Modify: `dreachy/static/index.html`
- Test: `tests/test_settings_routes.py`

**Interfaces:**
- Consumes: `DrupalClient.schema`, `.full_schema`, `.schema_discovered`, `.refresh_schema()` (Task 2); `parse_types`, `get_client`, `reset_client` (Task 3)
- Produces:
  - `GET /api/schema` → `{"discovered": bool, "types": [{"id": str, "label": str, "enabled": bool}, ...]}`
  - `POST /api/config` accepts `types: list[str] | None` (`None` leaves the saved selection alone; `[]` means every type) and returns `types_applied_immediately: bool`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings_routes.py`. Add `from _fake_site import NEWS_LABELS, NEWS_NODES, FakeSite` and `from dreachy.config import Config` to the imports.

```python
# ---------------------------------------------------------------------------
# Content types: discovered list with include/exclude, persisted as
# DREACHY_TYPES; saving rediscovers.
# ---------------------------------------------------------------------------


def test_get_schema_lists_discovered_types_and_which_are_enabled(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_BASE_URL", "https://example.com")
    site = FakeSite(NEWS_NODES, labels=NEWS_LABELS)
    shared._client = site.client(Config(enabled_types=("news_item",)), auto_discover=True)

    resp = _make_client().get("/api/schema")

    assert resp.json() == {
        "discovered": True,
        "types": [
            {"id": "event", "label": "Event", "enabled": False},
            {"id": "news_item", "label": "News item", "enabled": True},
        ],
    }


def test_get_schema_reports_the_fallback_when_the_site_cannot_be_read(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_BASE_URL", "https://example.com")
    shared._client = FakeSite(NEWS_NODES, index_status=503).client(auto_discover=True)

    body = _make_client().get("/api/schema").json()

    assert body["discovered"] is False
    assert [t["id"] for t in body["types"]] == ["article", "page", "recipe"]


def test_get_schema_contacts_no_site_before_one_is_configured() -> None:
    site = FakeSite(NEWS_NODES)
    shared._client = site.client()

    _make_client().get("/api/schema")

    assert site.requests == []


def test_post_config_saves_the_type_selection() -> None:
    client = _make_client()

    resp = client.post(
        "/api/config",
        json={"base_url": "https://example.com", "extra_instructions": "", "types": ["recipe", "article"]},
    )

    assert resp.json()["types_applied_immediately"] is True
    assert os.environ["DREACHY_TYPES"] == "recipe,article"
    env_text = (dreachy_main._instance_path() / ".env").read_text()
    assert "DREACHY_TYPES='recipe,article'" in env_text or "DREACHY_TYPES=recipe,article" in env_text
    assert shared.get_client().config.enabled_types == ("recipe", "article")


def test_post_config_saves_an_empty_selection_as_every_type(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_TYPES", "recipe")

    _make_client().post("/api/config", json={"base_url": "https://example.com", "extra_instructions": "", "types": []})

    assert os.environ["DREACHY_TYPES"] == ""
    assert shared.get_client().config.enabled_types == ()


def test_post_config_without_types_leaves_the_saved_selection_alone(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_TYPES", "recipe")

    resp = _make_client().post("/api/config", json={"base_url": "https://example.com", "extra_instructions": ""})

    assert resp.json()["types_applied_immediately"] is False
    assert os.environ["DREACHY_TYPES"] == "recipe"


def test_post_config_always_drops_the_cached_schema() -> None:
    shared._client = "sentinel-old-client"

    _make_client().post("/api/config", json={"base_url": "", "extra_instructions": ""})

    # Nothing changed, but saving is also how a newly created site type gets picked up.
    assert shared._client is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_settings_routes.py -q`
Expected: the new tests FAIL (`404` on `/api/schema`; `KeyError: 'types_applied_immediately'`).

- [ ] **Step 3: Implement the routes**

In `dreachy/main.py`, change `from dreachy.config import Config` to `from dreachy.config import Config, parse_types`. Add to `_ConfigPayload`:

```python
    # None = field absent (leave the saved selection alone); [] = every type
    # the site has, including ones added later.
    types: list[str] | None = None
```

In `_register_settings_routes`, add a new route after `get_config`:

```python
    @settings_app.get("/api/schema")
    def get_content_types() -> dict:
        client = get_client()
        # Retry a failed discovery whenever the page asks, but never before a
        # site is configured: that would go to the placeholder URL.
        if not client.schema_discovered and os.environ.get("DREACHY_BASE_URL"):
            client.refresh_schema()
        enabled = client.schema
        return {
            "discovered": client.schema_discovered,
            "types": [
                {"id": type_id, "label": type_schema.label, "enabled": type_id in enabled}
                for type_id, type_schema in client.full_schema.items()
            ],
        }
```

Replace the body of `save_config` with:

```python
    @settings_app.post("/api/config")
    def save_config(payload: _ConfigPayload) -> dict:
        env_path = _instance_path() / ".env"

        base_url = payload.base_url.strip()
        base_url_changed = False
        if base_url and base_url != os.environ.get("DREACHY_BASE_URL"):
            dotenv.set_key(str(env_path), "DREACHY_BASE_URL", base_url)
            os.environ["DREACHY_BASE_URL"] = base_url
            base_url_changed = True

        locale_changed = False
        if payload.locale is not None:
            locale = payload.locale.strip()
            if locale != os.environ.get("DREACHY_LOCALE"):
                dotenv.set_key(str(env_path), "DREACHY_LOCALE", locale)
                os.environ["DREACHY_LOCALE"] = locale
                locale_changed = True

        types_changed = False
        if payload.types is not None:
            types = ",".join(parse_types(",".join(payload.types)))
            if types != os.environ.get("DREACHY_TYPES", ""):
                dotenv.set_key(str(env_path), "DREACHY_TYPES", types)
                os.environ["DREACHY_TYPES"] = types
                types_changed = True

        # Always, even when nothing above changed: the next tool call picks up
        # the new settings with no restart, and rediscovers the site's content
        # types — saving is how a type created on the site since start shows up.
        reset_client()

        _write_extra_instructions(payload.extra_instructions)
        _render_profile()  # only takes effect on the next app start

        return {
            "status": "saved",
            "base_url_applied_immediately": base_url_changed,
            "locale_applied_immediately": locale_changed,
            "types_applied_immediately": types_changed,
            "instructions_require_restart": True,
        }
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q`
Expected: `86 passed`

- [ ] **Step 5: Add the content-types section to the settings page**

In `dreachy/static/index.html`:

(a) CSS: add before `button {`:

```css
		legend {
			font-weight: 600;
			margin-bottom: 0.4rem;
			padding: 0;
		}

		.type-option {
			display: flex;
			align-items: center;
			gap: 0.5rem;
			font-weight: 400;
			margin-bottom: 0.3rem;
		}
```

(b) HTML: insert after the language-prefix `</fieldset>`:

```html
		<fieldset>
			<legend>Content types</legend>
			<div id="types">
				<p class="hint">Loading…</p>
			</div>
			<p class="hint"><strong>Restart to apply</strong> to the search tool's type filter — it's built when
				Dreachy starts. Everything else applies immediately.</p>
			<p class="hint">What Dreachy talks about. Leave them all ticked to include every type, including ones
				the site adds later. Saving also re-reads the site's types.</p>
		</fieldset>
```

Also change the site-URL hint "Must expose JSON:API with anonymous read access." to "Must expose JSON:API with anonymous read access; Dreachy reads its content types from it."

(c) JS: add after the `const saveBtn = …` line:

```js
		const typesEl = document.getElementById("types");
		// True only while the boxes show the site's own types. On the built-in
		// fallback, or when the URL/prefix is changed in this same save, the boxes
		// don't describe the site being saved, so the saved selection is left alone.
		let typesFromSite = false;
		let loadedBaseUrl = "";
		let loadedLocale = "";

		async function loadTypes() {
			typesFromSite = false;
			try {
				const resp = await fetch("/api/schema");
				const data = await resp.json();
				const rows = data.types.map((type) => {
					const row = document.createElement("label");
					row.className = "type-option";
					const box = document.createElement("input");
					box.type = "checkbox";
					box.value = type.id;
					box.checked = type.enabled;
					box.disabled = !data.discovered;
					// A text node, never innerHTML: labels come from the site.
					row.append(box, document.createTextNode(`${type.label} (${type.id})`));
					return row;
				});
				if (!data.discovered) {
					const note = document.createElement("p");
					note.className = "hint";
					note.textContent = "Couldn't read content types from the site, so Dreachy is using its "
						+ "built-in defaults. Check the site URL, then save to try again.";
					rows.unshift(note);
				}
				typesEl.replaceChildren(...rows);
				typesFromSite = data.discovered;
			} catch (e) {
				typesEl.textContent = "Couldn't load content types.";
			}
		}

		function selectedTypes() {
			if (!typesFromSite) return null;
			if (baseUrlInput.value !== loadedBaseUrl || languagePrefixInput.value !== loadedLocale) return null;
			const boxes = [...typesEl.querySelectorAll("input[type=checkbox]")];
			const ticked = boxes.filter((box) => box.checked).map((box) => box.value);
			// All ticked (or none) means every type, including ones the site adds later.
			return ticked.length === boxes.length ? [] : ticked;
		}
```

In `loadConfig()`, after `languagePrefixInput.value = data.locale ?? "";`, add:

```js
				loadedBaseUrl = baseUrlInput.value;
				loadedLocale = languagePrefixInput.value;
```

In the submit handler's `JSON.stringify({...})`, add `types: selectedTypes(),` after `locale: languagePrefixInput.value,`. Replace the success `showStatus(...)` call with:

```js
				loadedBaseUrl = baseUrlInput.value;
				loadedLocale = languagePrefixInput.value;
				showStatus(
					"Saved. Site settings are active immediately; instruction changes apply next time Dreachy starts.",
					false
				);
				await loadTypes();
```

Replace the final `loadConfig();` with:

```js
		loadConfig();
		loadTypes();
```

- [ ] **Step 6: Check the page by hand**

Run a throwaway server against the fake news site:

```bash
uv run python - <<'EOF'
import os, sys
sys.path.insert(0, "tests")
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from _fake_site import NEWS_LABELS, NEWS_NODES, FakeSite
import dreachy.main as m, dreachy.tools._shared as shared
os.environ["DREACHY_BASE_URL"] = "https://example.com"
shared._client = FakeSite(NEWS_NODES, labels=NEWS_LABELS).client(auto_discover=True)
shared.reset_client = lambda: None  # keep the fake client across saves for this check
m.reset_client = shared.reset_client
m._instance_path = lambda: __import__("pathlib").Path(os.environ["TMPDIR"]) / "dreachy-settings-check"
m._instance_path().mkdir(exist_ok=True)
app = FastAPI(); m._register_settings_routes(app)
app.mount("/", StaticFiles(directory="dreachy/static", html=True))
uvicorn.run(app, port=8042)
EOF
```

Open `http://localhost:8042` and check each of these:
- "Event (event)" and "News item (news_item)" show as ticked, with the "Restart to apply" note directly beneath them.
- Unticking Event and saving shows the success message, and the boxes reload.
- Changing the URL and saving doesn't send `types`: the POST body in the browser's network tab has `"types": null`.

Stop the server with Ctrl-C.

- [ ] **Step 7: Commit**

```bash
git add dreachy/main.py dreachy/static/index.html tests/test_settings_routes.py
git commit -m "feat: settings page chooses which content types Dreachy talks about

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Tool specs and persona name the site's own types

**Files:**
- Modify: `dreachy/tools/_shared.py` (add `type_choices`, `or_list`)
- Modify: `dreachy/tools/drupal_find_content.py:15-35`
- Modify: `dreachy/tools/drupal_read_article.py:15-32`
- Modify: `dreachy/tools/drupal_whats_new.py:19-22`
- Modify: `dreachy/profile/dreachy/instructions.txt` (CAPABILITIES line for `drupal_read_article`)
- Test: `tests/test_tool_specs.py`

**Interfaces:**
- Consumes: `get_client().schema` (Tasks 2–3)
- Produces:
  - `type_choices() -> list[str]`
  - `or_list(names: list[str]) -> str`

- [ ] **Step 1: Write the failing tests**

`tests/test_tool_specs.py`:

```python
"""The tool specs the conversation app sends to the LLM name the site's own
content types. Specs are built once at start (after main._warm_schema), so
these read the shared client's schema, never the network."""

from __future__ import annotations

import pytest

import dreachy.tools._shared as shared
from dreachy.client import DrupalClient
from dreachy.config import Config
from dreachy.tools._shared import or_list
from dreachy.tools.drupal_find_content import DrupalFindContent
from dreachy.tools.drupal_read_article import DrupalReadArticle


@pytest.fixture(autouse=True)
def _reset_shared_client():
    shared._client = None
    yield
    shared._client = None


def _use(config: Config) -> None:
    shared._client = DrupalClient(config)


def test_find_content_spec_is_unchanged_on_umami() -> None:
    _use(Config())

    spec = DrupalFindContent().spec()

    assert spec["parameters"]["properties"]["content_type"]["enum"] == ["article", "page", "recipe"]
    assert spec["description"] == (
        "Search the site's content by keyword, optionally filtered to one content type "
        "(article, page, or recipe). Use when asked to find, search, or look up something "
        "on the site."
    )


def test_find_content_offers_the_sites_own_types() -> None:
    _use(Config(content_types=("news_item", "event")))

    spec = DrupalFindContent().spec()

    assert spec["parameters"]["properties"]["content_type"]["enum"] == ["news_item", "event"]
    assert "(news_item or event)" in spec["description"]


def test_find_content_offers_only_the_enabled_types() -> None:
    _use(Config(enabled_types=("recipe",)))

    assert DrupalFindContent().spec()["parameters"]["properties"]["content_type"]["enum"] == ["recipe"]


def test_read_article_names_the_sites_types() -> None:
    _use(Config())

    assert DrupalReadArticle().spec()["description"].startswith(
        "Fetch the full text of one article, page, or recipe by title or URL path, for reading aloud."
    )


def test_or_list_reads_naturally() -> None:
    assert or_list(["news_item"]) == "news_item"
    assert or_list(["a", "b"]) == "a or b"
    assert or_list(["a", "b", "c"]) == "a, b, or c"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_tool_specs.py -q`
Expected: collection error, `ImportError: cannot import name 'or_list'`

- [ ] **Step 3: Add the helpers to `_shared.py`**

Append:

```python
def type_choices() -> list[str]:
    """Machine names of the types Dreachy talks about, for tool specs. No network."""
    return list(get_client().schema)


def or_list(names: list[str]) -> str:
    """``["article", "page", "recipe"]`` -> ``"article, page, or recipe"``."""
    if len(names) <= 2:
        return " or ".join(names)
    return f"{', '.join(names[:-1])}, or {names[-1]}"
```

- [ ] **Step 4: Make the tool specs read the schema**

`dreachy/tools/drupal_find_content.py`: import `or_list, type_choices` alongside `get_client`. Replace the `description = (...)` and `parameters_schema = {...}` class attributes with:

```python
    # Properties, not class attributes: the conversation app builds tool
    # specs once at start, after main.py has discovered the site's types, so
    # these name the site's own. A type enabled later reaches the spec on the
    # next start; one disabled later makes DrupalClient.find_content widen
    # the search instead of failing.
    @property
    def description(self) -> str:
        return (
            "Search the site's content by keyword, optionally filtered to one content type "
            f"({or_list(type_choices())}). Use when asked to find, search, or look up something "
            "on the site."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "Keyword to search for in titles."},
                "content_type": {
                    "type": "string",
                    "enum": type_choices(),
                    "description": "Optional: restrict the search to one content type.",
                },
            },
            "required": ["keyword"],
        }
```

`dreachy/tools/drupal_read_article.py`: import `or_list, type_choices`. Replace the `description = (...)` class attribute with:

```python
    # A property for the same reason as DrupalFindContent's: it names the
    # site's own types, discovered before the specs are built.
    @property
    def description(self) -> str:
        return (
            f"Fetch the full text of one {or_list(type_choices())} by title or URL path, for "
            "reading aloud. Use when asked to read something, or to read a piece of content out loud."
        )
```

`dreachy/tools/drupal_whats_new.py`: replace the description with:

```python
    description = (
        "Get the latest content published on the site, newest first. Use when asked what's "
        "new, what's recent, or what's been posted lately."
    )
```

`dreachy/profile/dreachy/instructions.txt`: replace

```
- Read an article, page, or recipe aloud in full (drupal_read_article)
```

with

```
- Read any piece of the site's content aloud in full (drupal_read_article)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q`
Expected: `91 passed`

- [ ] **Step 6: Commit**

```bash
git add dreachy/tools dreachy/profile/dreachy/instructions.txt tests/test_tool_specs.py
git commit -m "feat: tool specs and persona name the site's own content types

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Docs, changelog, version, and the live check

**Files:**
- Modify: `README.md` (Requirements, Configuration, Known issues, Development)
- Modify: `.env.example`
- Create: `CHANGELOG.md`
- Modify: `pyproject.toml` (`version = "0.2.0"`)

- [ ] **Step 1: README**

- **Requirements:** replace the bullet starting "At least one content type among `article`, `page`, `recipe`…" with:

  > - At least one content type with a formatted text field (core's `body`, for example). Dreachy reads the site's content types and their text fields itself — see **Content types** under Configuration — so any content model works without code changes. The Umami demo profile's `article`/`page`/`recipe` mapping remains the built-in fallback for when the site can't be read.

- **Configuration:** add after the language-prefix bullet:

  > - **Content types** — the site's content types as Dreachy found them, one checkbox each. Leave them all ticked to include every type, including ones the site adds later. Takes effect immediately, except the search tool's type filter: restart Dreachy to apply that, as the page notes beside the checkboxes. Saving also re-reads the site's types, so a type created on the site since start shows up after a save.

  Change "No SSH needed for either of those." to "No SSH needed for any of those." Change "both settings are backed by a `.env` file" to "these settings are backed by a `.env` file".

- **Known issues:** replace the "The content model is Umami's" bullet with:

  > - **Content-type discovery reads real content.** Anonymous JSON:API can't see field definitions, so Dreachy finds a type's text by looking at its five most recent nodes. It takes a formatted text field as the body (`body` preferred, then `field_body`, then any other) and a field named like `summary` or `teaser` as the teaser. A type with no content yet gets a best guess (`body`/`field_body`) until the next save or restart. Plain (unformatted) long-text fields aren't read.
  > - **If the site can't be read at start**, Dreachy falls back to the Umami `article`/`page`/`recipe` mapping and retries discovery, at most once a minute, as it's used.

- **Development:** replace the `client.py` / `config.py` bullet with:

  > - `client.py` / `config.py` — a small sync wrapper over Drupal's JSON:API, which discovers the site's content model at start.
  > - `schema.py` — the content-model heuristics (which types, which fields hold text). `client.py` fetches; `schema.py` decides.

  In the paragraph about `dreachy/main.py`, append after "…and a rendered copy of the profile.": "It also loads that `.env` and discovers the site's content types before the conversation app builds its tool specs, and serves `GET /api/schema` for the settings page's content-type checkboxes."

- [ ] **Step 2: `.env.example`**

Replace "Must expose JSON:API with anonymous read access to article/page/recipe nodes. See README.md "Requirements" for the exact content model Dreachy expects." with "Must expose JSON:API with anonymous read access to the content Dreachy should talk about. See README.md "Requirements"." Then add after the language-prefix block:

```
# --- Optional: content types ---
# Comma-separated machine names of the content types Dreachy talks about,
# normally set from the settings page's checkboxes. Empty or absent means
# every type the site has, including ones added later.
# DREACHY_TYPES=article,recipe
```

- [ ] **Step 3: `CHANGELOG.md`**

```markdown
# Changelog

## 0.2.0 — unreleased

Schema-driven content model (`specs/backend-and-auth.md`, R1).

- Dreachy reads the site's content types, and which of their fields hold the
  text, from the site itself: any content model works without code changes.
  The Umami mapping is now only the fallback for when the site can't be read.
- Settings page: choose which content types Dreachy talks about
  (`DREACHY_TYPES`). Saving re-reads the site's types.
- The instance `.env` is loaded before the conversation app starts, so the
  search tool's type filter lists the site's own types.
- Reading by path no longer returns content of a type Dreachy has been told
  to leave out.

## 0.1.0

Initial release.
```

- [ ] **Step 4: Version bump**

In `pyproject.toml`, change `version = "0.1.0"` to `version = "0.2.0"`.

- [ ] **Step 5: Full test run**

Run: `uv run pytest -q`
Expected: `91 passed`

- [ ] **Step 6: Commit**

```bash
git add README.md .env.example CHANGELOG.md pyproject.toml
git commit -m "docs: schema-driven content model — README, settings, changelog

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Live check against the Umami sandbox (manual, needs network, run by Vincenzo)**

```bash
DREACHY_BASE_URL=<sandbox URL> uv run python - <<'EOF'
from dreachy.client import DrupalClient
from dreachy.config import Config
from dreachy.schema import FALLBACK_TYPES
from dreachy.tool_queries import drupal_find_content, drupal_site_pulse, drupal_whats_new

with DrupalClient(Config.from_env(), auto_discover=True) as client:
    discovered = client.get_schema()
    print(discovered)
    for bundle, known in FALLBACK_TYPES.items():
        assert (discovered[bundle].text_fields, discovered[bundle].summary_field) == (
            known.text_fields, known.summary_field), bundle
    assert client.refresh_schema()
    print(drupal_whats_new(client))
    print(drupal_find_content(client, keyword="salad"))
    print(drupal_site_pulse(client))
EOF
```

Expected: no assertion errors. The what's-new, find and pulse output matches a pre-R1 run (`git stash` or `git switch main` to compare).

Then on the robot:
- The settings page lists Article, Basic page and Recipe, all ticked.
- Untick Basic page and save; "what's new?" no longer mentions pages.
- Re-tick and save.
- "Read me the borscht recipe" still reads in full.
- A new article still makes the watcher perk up.

**Stop here: R1 release boundary, for review.**
