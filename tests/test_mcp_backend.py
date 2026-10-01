"""McpBackend reads (R4 Task 5) against FakeMcpSite + FakeEntityStore, seeded
with the sandbox's recorded field definitions. Reads share one long-lived
session; node dicts come from one list call per field, never per-item loads
(R4 latency ruling)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from _fake_mcp import FakeEntityStore, FakeMcpSite, FakeTokenEndpoint

from dreachy.backend import DreachySiteError
from dreachy.config import Config
from dreachy.mcp_backend import McpBackend

_RECORDED = Path(__file__).parent / "fixtures" / "mcp" / "responses"


def _definitions(name: str) -> dict[str, Any]:
    return json.loads((_RECORDED / name).read_text())["result"]["structuredContent"]["data"]


DEFINITIONS = {"standard_page": _definitions("20-definitions-standard-page.json"), "news": _definitions("49-defs-news.json")}

JAN_2025, SEP_2026, NEWEST = "1735689600", "1790000000", "1790500000"


def _node(bundle: str, title: str, created: str, *, status: str = "1", state: str | None = "published", **fields) -> dict:
    return {
        "bundle": bundle,
        "uuid": f"uuid-{title.lower().replace(' ', '-')}",
        "fields": {
            "title": title,
            "status": status,
            "moderation_state": state,
            "created": created,
            "changed": created,
            "path": {"alias": fields.pop("alias", None), "pid": None, "langcode": "en"},
            **fields,
        },
    }


def _store() -> FakeEntityStore:
    return FakeEntityStore(
        {
            5: _node("standard_page", "Admissions", JAN_2025, alias="/admissions", description="<p>How to <b>apply</b>.</p>", preview_text="Start here."),
            6: _node("news", "Open day", SEP_2026, description="<p>Open day news.</p>", preview_text="A day."),
            7: _node("news", "Budget draft", NEWEST, status="0", state="draft", description="<p>Budget.</p>"),
            8: _node("student_announcement", "Selling board", SEP_2026, state=None, description="<p>Bikes.</p>"),
        },
        definitions=DEFINITIONS,  # student_announcement's definitions are refused
    )


def _backend(store: FakeEntityStore, **settings) -> tuple[McpBackend, FakeMcpSite]:
    site = FakeMcpSite(store.tools())
    config = Config(
        base_url="https://site.test",
        auth="oauth",
        oauth_client_id="dreachy",
        oauth_client_secret="s",
        backend="mcp",
        **{"mcp_search_index": "content_vector", **settings},
    )
    backend = McpBackend(config, http_client_factory=site.client_factory(), token_http_client=FakeTokenEndpoint().client())
    return backend, site


@pytest.fixture
def discovered():
    backends: list[McpBackend] = []

    def make(store: FakeEntityStore | None = None, **settings) -> tuple[McpBackend, FakeMcpSite]:
        backend, site = _backend(store or _store(), **settings)
        backends.append(backend)
        assert backend.refresh_schema()
        return backend, site

    yield make
    for backend in backends:
        backend.close()


def _tool_calls(site: FakeMcpSite) -> list[str]:
    return [name.removeprefix("tool_api__demo_") for _, name, _ in site.calls]


# -- schema ----------------------------------------------------------------


def test_schema_picks_text_fields_by_type(discovered) -> None:
    backend, _ = discovered()

    schema = backend.full_schema

    assert set(schema) == {"news", "standard_page"}  # student_announcement's definitions are refused: skipped
    page = schema["standard_page"]
    assert page.text_fields[0] == "description"
    assert page.label == "Standard page"
    assert page.moderated
    assert page.required_text == ("preview_text",)
    assert page.required_other == ("ai_automator_status",)
    assert set(schema["news"].required_other) == {"ai_automator_status", "news_publish_date"}


def test_the_bundle_list_comes_from_one_listing_of_all_content(discovered) -> None:
    _, site = discovered()

    lists = [args for _, name, args in site.calls if name.endswith("entity_list")]
    assert lists == [{"entity_type_id": "node", "amount": 0, "sort_field": "created", "sort_order": "DESC", "fields": "moderation_state"}]


def test_the_bundle_setting_replaces_the_listing(discovered) -> None:
    backend, site = discovered(mcp_bundles=("news",))

    assert set(backend.full_schema) == {"news"}
    assert "entity_list" not in _tool_calls(site)


# -- recent ----------------------------------------------------------------


def test_recent_is_published_only_and_newest_first(discovered) -> None:
    backend, _ = discovered()

    assert [n["title"] for n in backend.get_recent_nodes(5)] == ["Open day", "Admissions"]
    assert [n["title"] for n in backend.get_recent_nodes(5, include_unpublished=True)] == ["Budget draft", "Open day", "Admissions"]


def test_node_dicts_keep_the_backend_shape(discovered) -> None:
    backend, _ = discovered()

    admissions = backend.get_recent_nodes(5)[-1]

    assert admissions == {
        "id": "uuid-admissions",
        "title": "Admissions",
        "type": "standard_page",
        "created": "2025-01-01T00:00:00+00:00",
        "changed": "2025-01-01T00:00:00+00:00",
        "path": "/admissions",
        "body": "How to apply .",
        "summary": "How to apply .",
        "status": True,
        "moderation_state": "published",
    }


def test_recent_builds_node_dicts_from_list_calls_not_loads(discovered) -> None:
    backend, site = discovered()
    site.calls.clear()

    backend.get_recent_nodes(5)

    assert set(_tool_calls(site)) == {"entity_list"}
    assert all(args.get("fields") for _, _, args in site.calls)  # one field per call


def test_the_watchers_poll_lists_a_small_window(discovered) -> None:
    backend, site = discovered()
    site.calls.clear()

    assert [n["title"] for n in backend.get_recent_nodes(1)] == ["Open day"]
    assert {args["amount"] for _, _, args in site.calls} == {10}


def test_a_list_call_the_server_drops_once_is_retried(discovered) -> None:
    backend, site = discovered()
    site.fail_next = 1  # one HTTP 503, as the sandbox gives under load

    assert [n["title"] for n in backend.get_recent_nodes(5)] == ["Open day", "Admissions"]
    assert len(set(site.session_ids())) == 1  # retried in place, not by reconnecting


# -- find ------------------------------------------------------------------


def test_find_dedupes_chunks_of_the_same_node(discovered) -> None:
    backend, _ = discovered()

    assert [n["title"] for n in backend.find_content("open")] == ["Open day"]


def test_find_never_sends_check_access_false(discovered) -> None:
    store = _store()
    backend, _ = discovered(store, mcp_mapping={"search_params": {"check_access": False}})

    backend.find_content("open")
    backend.get_article("Admissions")

    assert store.searches and all(search["check_access"] is True for search in store.searches)


def test_find_drops_unpublished_unless_asked(discovered) -> None:
    backend, _ = discovered()

    assert backend.find_content("budget") == []
    assert [n["title"] for n in backend.find_content("budget", include_unpublished=True)] == ["Budget draft"]


def test_find_filters_by_type_and_widens_an_unknown_one(discovered) -> None:
    backend, _ = discovered()

    assert [n["title"] for n in backend.find_content("day", content_type="standard_page")] == []
    assert [n["title"] for n in backend.find_content("day", content_type="no_such_type")] == ["Open day"]


def test_find_reads_each_hit_with_one_load_and_one_values_call(discovered) -> None:
    backend, site = discovered()
    site.calls.clear()

    backend.find_content("open")

    assert _tool_calls(site) == ["search_index", "entity_load_by_id", "entity_field_values"]
    assert "fields" not in site.calls[-1][2]  # every field in one call


# -- read ------------------------------------------------------------------


def test_get_article_resolves_through_search_load_values(discovered) -> None:
    backend, site = discovered()

    article = backend.get_article("Admissions")

    assert article["title"] == "Admissions"
    assert article["body"] == "How to apply ."
    assert len(set(site.session_ids())) == 1


def test_a_path_is_searched_as_words(discovered) -> None:
    store = _store()
    backend, _ = discovered(store)

    assert backend.get_article("/admissions")["title"] == "Admissions"
    assert store.searches[-1]["search_words"] == "admissions"


def test_get_article_prefers_an_exact_title(discovered) -> None:
    store = _store()
    # Ranked first by the (fake) index, but not the title asked for.
    store.nodes = {4: _node("standard_page", "Visit campus", JAN_2025, description="<p>Come to the open day.</p>"), **store.nodes}
    backend, _ = discovered(store)

    assert backend.get_article("open day")["title"] == "Open day"


def test_get_article_is_none_when_nothing_published_matches(discovered) -> None:
    backend, _ = discovered()

    assert backend.get_article("nothing like it") is None
    assert backend.get_article("Budget draft") is None
    assert backend.get_article("Budget draft", include_unpublished=True)["title"] == "Budget draft"


def test_no_search_index_is_a_clear_site_error(discovered) -> None:
    backend, _ = discovered(mcp_search_index="")

    with pytest.raises(DreachySiteError, match="search index"):
        backend.find_content("open")


# -- sessions and failures -------------------------------------------------


def test_reads_share_one_session(discovered) -> None:
    backend, site = discovered()

    backend.get_recent_nodes(5)
    backend.find_content("open")
    backend.get_article("Admissions")

    assert len(set(site.session_ids())) == 1


@pytest.mark.parametrize(
    "read",
    [
        lambda b: b.get_schema(),
        lambda b: b.get_recent_nodes(5),
        lambda b: b.find_content("open"),
        lambda b: b.get_article("Admissions"),
    ],
)
def test_a_read_failure_is_a_site_error(read) -> None:
    backend, site = _backend(_store())
    site.down = True
    try:
        with pytest.raises(DreachySiteError):
            read(backend)
    finally:
        backend.close()


def test_a_failed_discovery_keeps_the_fallback() -> None:
    backend, site = _backend(_store())
    site.down = True
    try:
        assert not backend.refresh_schema()
        assert backend.last_discovery_problem == "site_unreachable"
    finally:
        backend.close()
