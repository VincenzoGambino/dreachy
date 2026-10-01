"""drupal_site_action (R4 Task 8): allowlisted MCP extras, never publishing.

Against FakeMcpSite serving the sandbox's recorded canvas tools (names,
schemas, annotations). The effective allowlist is DREACHY_MCP_EXTRA_TOOLS ∩
what the server offers ∖ the hard deny-list (Ruling 1, extended)."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from _fake_mcp import FakeEntityStore, FakeMcpSite, FakeTokenEndpoint

import dreachy.main as dreachy_main
import dreachy.tools._shared as shared
from dreachy.backend import DreachySiteError
from dreachy.client import JsonApiBackend
from dreachy.config import Config
from dreachy.mcp_backend import McpBackend
from dreachy.tools.drupal_site_action import DrupalSiteAction

_RECORDED = json.loads((Path(__file__).parent / "fixtures" / "mcp" / "tools_list.json").read_text())
CANVAS = {tool["name"]: tool for tool in _RECORDED if "canvas" in tool["name"]}
DENIED = [
    "tool_api__canvas_publish_auto_saves",
    "tool_api__canvas_set_homepage",
    "tool_api__canvas_delete_page",
    "tool_api__canvas_discard_auto_save",
    "tool_api__canvas_set_default_page_variant",
]


class _Site(FakeMcpSite):
    """The fake, describing the canvas tools with their recorded schemas."""

    def _describe(self, name: str) -> dict[str, Any]:
        recorded = CANVAS.get(name)
        if recorded is None:
            return super()._describe(name)
        return {k: v for k, v in recorded.items() if k in ("name", "description", "inputSchema", "annotations")}


def _echo(arguments, state):
    return {"echo": arguments}


def _site(extra: dict | None = None) -> _Site:
    tools = {**FakeEntityStore().tools(), **{name: _echo for name in CANVAS}, **(extra or {})}
    return _Site(tools, annotations={"tool_api__demo_peek": {"readOnlyHint": True}, "tool_api__demo_search_with_access": {"readOnlyHint": True}})


@pytest.fixture
def backend_for():
    made: list[McpBackend] = []

    def make(site: _Site, extras: tuple[str, ...]) -> McpBackend:
        config = Config(
            base_url="https://site.test",
            auth="oauth",
            oauth_client_id="dreachy",
            oauth_client_secret="s",
            backend="mcp",
            mcp_extra_tools=extras,
        )
        backend = McpBackend(config, http_client_factory=site.client_factory(), token_http_client=FakeTokenEndpoint().client())
        made.append(backend)
        return backend

    yield make
    for backend in made:
        backend.close()
    shared._client = None


def _call(backend, **kwargs) -> dict[str, Any]:
    shared._client = backend
    return asyncio.run(DrupalSiteAction()(None, **kwargs))


def _called(site: FakeMcpSite) -> list[str]:
    return [name for _, name, _ in site.calls]


def test_publish_homepage_delete_discard_and_set_default_are_never_exposed(backend_for) -> None:
    site = _site()
    backend = backend_for(site, (*DENIED, "tool_api__canvas_list_targets"))

    assert list(backend.site_actions()) == ["tool_api__canvas_list_targets"]
    for name in DENIED:
        with pytest.raises(DreachySiteError):
            backend.run_site_action(name, {}, confirmed=True)
        assert "not available" in _call(backend, action=name, arguments={}, confirmed=True)["error"]
    assert not set(DENIED) & set(_called(site))


def test_extras_are_allowlist_only(backend_for) -> None:
    site = _site()
    backend = backend_for(site, ("tool_api__canvas_list_targets", "tool_api__canvas_not_on_this_site"))

    assert list(backend.site_actions()) == ["tool_api__canvas_list_targets"]
    assert "not available" in _call(backend, action="tool_api__canvas_list_components", arguments={}, confirmed=True)["error"]
    assert "tool_api__canvas_list_components" not in _called(site)


def test_an_extra_without_a_read_only_hint_needs_confirmation(backend_for) -> None:
    # Every tool the sandbox serves says readOnlyHint: false — so every extra asks first.
    site = _site()
    backend = backend_for(site, ("tool_api__canvas_list_targets",))

    refused = _call(backend, action="tool_api__canvas_list_targets", arguments={"limit": 5})
    assert "shall I" in refused["error"]
    assert "tool_api__canvas_list_targets" not in _called(site)
    with pytest.raises(DreachySiteError):
        backend.run_site_action("tool_api__canvas_list_targets", {"limit": 5}, confirmed=False)

    done = _call(backend, action="tool_api__canvas_list_targets", arguments={"limit": 5}, confirmed=True)

    assert done["result"] == {"echo": {"limit": 5}}


def test_a_write_extra_runs_in_a_session_of_its_own(backend_for) -> None:
    site = _site()
    backend = backend_for(site, ("tool_api__canvas_list_targets", "tool_api__demo_peek"))
    site.tools["tool_api__demo_peek"] = _echo
    backend.run_site_action("tool_api__demo_peek", {}, confirmed=False)
    read_session = site.session_ids()[-1]

    backend.run_site_action("tool_api__canvas_list_targets", {}, confirmed=True)

    assert site.session_ids()[-1] != read_session


def test_a_read_only_extra_runs_without_confirmation(backend_for) -> None:
    site = _site({"tool_api__demo_peek": _echo})
    backend = backend_for(site, ("tool_api__demo_peek",))

    assert _call(backend, action="tool_api__demo_peek", arguments={"x": 1})["result"] == {"echo": {"x": 1}}


def test_extras_cannot_switch_off_check_access(backend_for) -> None:
    site = _site({"tool_api__demo_search_with_access": _echo})
    original = site._describe

    def describe(name: str) -> dict[str, Any]:
        tool = original(name)
        if name == "tool_api__demo_search_with_access":
            tool["inputSchema"] = {"type": "object", "properties": {"q": {"type": "string"}, "check_access": {"type": "boolean"}}}
        return tool

    site._describe = describe
    backend = backend_for(site, ("tool_api__demo_search_with_access",))

    result = backend.run_site_action("tool_api__demo_search_with_access", {"q": "x", "check_access": False}, confirmed=False)

    assert result["result"] == {"echo": {"q": "x", "check_access": True}}


def test_an_extra_never_asks_to_publish(backend_for) -> None:
    site = _site()
    backend = backend_for(site, ("tool_api__canvas_create_page", "tool_api__canvas_update_page_metadata"))

    # create_page's `published` defaults to TRUE: it's always sent as false.
    created = backend.run_site_action("tool_api__canvas_create_page", {"title": "Open day"}, confirmed=True)
    asked = backend.run_site_action("tool_api__canvas_create_page", {"title": "x", "published": True}, confirmed=True)
    # update's `published` left unset keeps the current status: left alone, but never true.
    untouched = backend.run_site_action("tool_api__canvas_update_page_metadata", {"page_id": 3, "title": "y"}, confirmed=True)
    refused = backend.run_site_action("tool_api__canvas_update_page_metadata", {"page_id": 3, "published": True}, confirmed=True)

    assert created["result"]["echo"] == {"title": "Open day", "published": False}
    assert asked["result"]["echo"]["published"] is False
    assert untouched["result"]["echo"] == {"page_id": 3, "title": "y"}
    assert refused["result"]["echo"]["published"] is False


def test_a_denied_entry_is_logged_once(backend_for, caplog) -> None:
    backend = backend_for(_site(), ("tool_api__canvas_publish_auto_saves",))

    with caplog.at_level(logging.WARNING):
        backend.site_actions()
        backend.site_actions()

    assert caplog.text.count("tool_api__canvas_publish_auto_saves") == 1


def test_arguments_must_be_an_object(backend_for) -> None:
    backend = backend_for(_site(), ("tool_api__canvas_list_targets",))
    backend.site_actions()

    assert "error" in _call(backend, action="tool_api__canvas_list_targets", arguments="limit=5", confirmed=True)


def test_a_large_result_is_cut_short(backend_for) -> None:
    site = _site({"tool_api__demo_peek": lambda arguments, state: {"rows": ["x" * 100] * 200}})
    backend = backend_for(site, ("tool_api__demo_peek",))

    result = backend.run_site_action("tool_api__demo_peek", {}, confirmed=False)

    assert "result" not in result
    assert len(result["result_preview"]) <= 4000


def test_the_spec_names_the_allowed_actions(backend_for) -> None:
    site = _site({"tool_api__demo_peek": _echo})
    backend = backend_for(site, ("tool_api__canvas_list_targets", "tool_api__demo_peek"))
    backend.site_actions()  # at start, as main does
    shared._client = backend

    spec = DrupalSiteAction().spec()

    assert spec["parameters"]["properties"]["action"]["enum"] == ["tool_api__canvas_list_targets", "tool_api__demo_peek"]
    assert "tool_api__canvas_list_targets" in spec["description"]
    assert "ask first" in spec["description"]


def test_without_the_mcp_backend_there_are_no_site_actions() -> None:
    backend = JsonApiBackend(Config(base_url="https://site.test"))
    try:
        assert backend.site_actions() == {}
        shared._client = backend
        assert "not available" in asyncio.run(DrupalSiteAction()(None, action="x", arguments={}, confirmed=True))["error"]
    finally:
        backend.close()
        shared._client = None


def test_site_action_is_registered_only_with_an_mcp_allowlist(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dreachy_main, "_instance_path", lambda: tmp_path)
    monkeypatch.setattr(dreachy_main, "_editorial_enabled", False)

    monkeypatch.setattr(dreachy_main, "_site_actions_enabled", True)
    dreachy_main._render_profile()
    with_actions = (tmp_path / "profile" / "dreachy" / "tools.txt").read_text()

    monkeypatch.setattr(dreachy_main, "_site_actions_enabled", False)
    dreachy_main._render_profile()
    without = (tmp_path / "profile" / "dreachy" / "tools.txt").read_text()

    assert "drupal_site_action" in with_actions
    assert "drupal_site_action" not in without


def test_site_actions_are_decided_at_start_from_the_backend(monkeypatch, backend_for) -> None:
    backend = backend_for(_site(), ("tool_api__canvas_list_targets",))
    shared._client = backend
    assert dreachy_main._site_actions_available()

    shared._client = backend_for(_site(), ())
    assert not dreachy_main._site_actions_available()
