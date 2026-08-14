"""Mocked tests for drupal_watch_site's start/stop state machine and change detection.

No live site, no real robot: DrupalClient is mocked via httpx.MockTransport (same
pattern as test_client.py); play_reaction is monkeypatched to a recording stub.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from dreachy.client import DrupalClient
from dreachy.config import Config
from dreachy.tools import drupal_watch_site as watch_module
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


class _FakeMedia:
    def __init__(self) -> None:
        self.played_sounds: list[str] = []

    def play_sound(self, sound_file: str) -> None:
        self.played_sounds.append(sound_file)


class _FakeReachyMini:
    def __init__(self) -> None:
        self.media = _FakeMedia()


def _node(uuid: str, title: str, created: str) -> dict:
    return {
        "type": "node--article",
        "id": uuid,
        "attributes": {
            "title": title,
            "created": created,
            "changed": created,
            "path": {"alias": f"/n/{uuid}"},
            "field_body": {"value": "<p>...</p>"},
        },
    }


@pytest.fixture(autouse=True)
def _reset_watch_state():
    """Reset the module-level watch task before and after each test."""
    watch_module._watch_task = None
    yield
    if watch_module._watch_task is not None and not watch_module._watch_task.done():
        watch_module._watch_task.cancel()
    watch_module._watch_task = None


def _make_client(handler, poll_interval: float = 0.01) -> DrupalClient:
    transport = httpx.MockTransport(handler)
    config = Config(poll_interval_seconds=poll_interval)
    return DrupalClient(config, http_client=httpx.Client(transport=transport))


def test_start_reports_watching_then_already_watching(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    client = _make_client(handler)
    monkeypatch.setattr(watch_module, "get_client", lambda: client)

    deps = ToolDependencies(reachy_mini=None, movement_manager=None)
    tool = watch_module.DrupalWatchSite()

    async def run_test():
        first = await tool(deps, action="start")
        second = await tool(deps, action="start")
        stopped = await tool(deps, action="stop")
        return first, second, stopped

    first, second, stopped = asyncio.run(run_test())

    assert first["status"] == "watching"
    assert second["status"] == "already watching"
    assert stopped["status"] == "stopped watching"


def test_stop_when_not_watching_reports_not_watching() -> None:
    deps = ToolDependencies(reachy_mini=None, movement_manager=None)
    tool = watch_module.DrupalWatchSite()

    result = asyncio.run(tool(deps, action="stop"))

    assert result["status"] == "not watching"


def test_invalid_action_returns_error() -> None:
    deps = ToolDependencies(reachy_mini=None, movement_manager=None)
    tool = watch_module.DrupalWatchSite()

    result = asyncio.run(tool(deps, action="invalid"))

    assert "error" in result


def test_watch_loop_reacts_only_to_genuinely_new_content(monkeypatch) -> None:
    responses = [
        [_node("a1", "Original", "2026-07-20T00:00:00+00:00")],
        [_node("a1", "Original", "2026-07-20T00:00:00+00:00")],  # unchanged
        [_node("a2", "Brand new", "2026-07-27T00:00:00+00:00")],  # newer!
    ]
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/en/jsonapi/node/article":
            idx = min(call_count["n"], len(responses) - 1)
            call_count["n"] += 1
            return httpx.Response(200, json={"data": responses[idx]})
        return httpx.Response(200, json={"data": []})

    client = _make_client(handler, poll_interval=0.01)
    monkeypatch.setattr(watch_module, "get_client", lambda: client)

    played: list[str] = []

    async def fake_play_reaction(reaction, deps):
        played.append(reaction.name)

    monkeypatch.setattr(watch_module, "play_reaction", fake_play_reaction)

    deps = ToolDependencies(reachy_mini=_FakeReachyMini(), movement_manager=None)
    tool = watch_module.DrupalWatchSite()

    async def run_test():
        await tool(deps, action="start")
        await asyncio.sleep(0.5)  # let several poll cycles happen
        await tool(deps, action="stop")

    asyncio.run(run_test())

    assert played == ["perk_up"]
    assert deps.reachy_mini.media.played_sounds == ["wake_up.wav"]
