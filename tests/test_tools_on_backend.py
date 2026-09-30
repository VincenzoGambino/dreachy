"""The five tools, called as the conversation app calls them, against an
in-memory Backend (spec R2 check: "all five tools green against mocked
backend")."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from _fake_backend import FakeBackend, fake_node

import dreachy.tools._shared as shared
from dreachy.config import Config
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


def test_no_tool_asks_for_unpublished_content() -> None:
    backend = shared._client
    _call(DrupalWhatsNew())
    _call(DrupalFindContent(), keyword="park")
    _call(DrupalSitePulse())
    _call(DrupalReadArticle(), title_or_path="Harvest fair")
    asyncio.run(watch_module._latest_created(backend))

    assert backend.unpublished_requests and not any(backend.unpublished_requests)


# ---------------------------------------------------------------------------
# Editorial tools (R3)
# ---------------------------------------------------------------------------

# content_types: the fake has no discovery, so this order decides "the first enabled type" (news_item).
_LOGGED_IN = dict(
    auth="oauth", oauth_client_id="dreachy", oauth_client_secret="s3cret", content_types=("news_item", "event")
)
_EDITORIAL = [
    *NODES,
    fake_node("d1", "Park opening hours", "news_item", _iso(0.5), status=False, moderation_state="draft"),
    fake_node("r1", "Budget report", "news_item", _iso(2), status=False, moderation_state="review"),
    fake_node("x1", "Old fair", "news_item", _iso(90), status=False, moderation_state="archived"),
    fake_node("e2", "Winter market", "event", _iso(4), status=False),
]


def _editor(**config) -> FakeBackend:
    backend = FakeBackend(list(_EDITORIAL), Config(**{**_LOGGED_IN, **config}), editable=True)
    shared._client = backend
    return backend


def test_pending_content_summarises_counts_and_latest_titles() -> None:
    from dreachy.tools.drupal_pending_content import DrupalPendingContent

    _editor()

    assert _call(DrupalPendingContent()) == {
        "count": 3,
        "by_state": {"draft": 1, "review": 1, "unpublished": 1},
        "latest": [
            {"title": "Park opening hours", "type": "news_item", "state": "draft", "age": "today"},
            {"title": "Budget report", "type": "news_item", "state": "review", "age": "2 days ago"},
            {"title": "Winter market", "type": "event", "state": "unpublished", "age": "4 days ago"},
        ],
    }


@pytest.mark.parametrize("confirmed", [None, False, "true", 1])
def test_create_note_refuses_anything_but_confirmed_true(confirmed) -> None:
    from dreachy.tools.drupal_create_note import DrupalCreateNote

    backend = _editor()
    kwargs = {"title": "Park bench", "body": "Needs painting."}
    if confirmed is not None:
        kwargs["confirmed"] = confirmed

    result = _call(DrupalCreateNote(), **kwargs)

    assert "error" in result and "shall I save it as a draft?" in result["error"]
    assert backend.created == []


def test_create_note_saves_a_draft_once_confirmed() -> None:
    from dreachy.tools.drupal_create_note import DrupalCreateNote

    backend = _editor()

    result = _call(DrupalCreateNote(), title="Park bench", body="Needs painting.", confirmed=True)

    assert result == {"saved": "draft", "title": "Park bench", "type": "news_item"}
    assert backend.created == [("news_item", "Park bench", "Needs painting.")]


def test_create_note_uses_the_configured_note_type() -> None:
    from dreachy.tools.drupal_create_note import DrupalCreateNote

    backend = _editor(note_type="event")

    _call(DrupalCreateNote(), title="T", body="B", confirmed=True)

    assert backend.created[0][0] == "event"


def test_create_note_needs_a_title_and_a_body() -> None:
    from dreachy.tools.drupal_create_note import DrupalCreateNote

    backend = _editor()

    assert "error" in _call(DrupalCreateNote(), title="", body="B", confirmed=True)
    assert backend.created == []


def test_editorial_tools_refuse_without_a_login() -> None:
    from dreachy.tools.drupal_create_note import DrupalCreateNote
    from dreachy.tools.drupal_pending_content import DrupalPendingContent

    # Anonymous config, and a backend that fails on any call: had a tool
    # touched the site, the answer would be a site error, not this refusal.
    backend = FakeBackend(list(_EDITORIAL), fail=True)
    shared._client = backend

    assert _call(DrupalPendingContent()) == {"error": "Editing isn't available on this site."}
    assert _call(DrupalCreateNote(), title="T", body="B", confirmed=True) == {
        "error": "Editing isn't available on this site."
    }
    assert backend.created == []
