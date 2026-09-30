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
