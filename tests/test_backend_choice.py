"""Choosing the backend (R4 Task 7): DREACHY_BACKEND and the MCP settings,
read from the environment like every other setting."""

from __future__ import annotations

import logging

import pytest

import dreachy.tools._shared as shared
from dreachy.client import JsonApiBackend
from dreachy.config import Config
from dreachy.mcp_backend import McpBackend

_KEYS = (
    "DREACHY_BASE_URL",
    "DREACHY_AUTH",
    "DREACHY_OAUTH_CLIENT_ID",
    "DREACHY_OAUTH_CLIENT_SECRET",
    "DREACHY_BACKEND",
    "DREACHY_MCP_ENDPOINT",
    "DREACHY_MCP_SEARCH_INDEX",
    "DREACHY_MCP_MAPPING",
    "DREACHY_MCP_BUNDLES",
    "DREACHY_MCP_EXTRA_TOOLS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DREACHY_BASE_URL", "https://site.test")
    shared._client = None
    yield
    if shared._client is not None:
        shared._client.close()
    shared._client = None


def _login(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_AUTH", "oauth")
    monkeypatch.setenv("DREACHY_OAUTH_CLIENT_ID", "dreachy")
    monkeypatch.setenv("DREACHY_OAUTH_CLIENT_SECRET", "s")


def test_jsonapi_stays_the_default() -> None:
    assert Config.from_env().backend == "jsonapi"
    assert isinstance(shared.get_client(), JsonApiBackend)


def test_mcp_backend_is_chosen_by_setting(monkeypatch) -> None:
    _login(monkeypatch)
    monkeypatch.setenv("DREACHY_BACKEND", "MCP ")

    client = shared.get_client()

    assert isinstance(client, McpBackend)
    assert client.config.effective_mcp_endpoint == "https://site.test/mcp"


def test_an_unknown_backend_falls_back_to_jsonapi(monkeypatch, caplog) -> None:
    monkeypatch.setenv("DREACHY_BACKEND", "graphql")

    with caplog.at_level(logging.WARNING):
        assert Config.from_env().backend == "jsonapi"

    assert "graphql" in caplog.text


def test_the_mcp_settings_are_read(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_MCP_ENDPOINT", "https://site.test/api/mcp")
    monkeypatch.setenv("DREACHY_MCP_SEARCH_INDEX", " content_vector ")
    monkeypatch.setenv("DREACHY_MCP_MAPPING", '{"search": "custom_search", "search_params": {"min_score": 0.4}}')
    monkeypatch.setenv("DREACHY_MCP_BUNDLES", "news, standard_page,")
    monkeypatch.setenv("DREACHY_MCP_EXTRA_TOOLS", "tool_api__canvas_list_pages")

    config = Config.from_env()

    assert config.effective_mcp_endpoint == "https://site.test/api/mcp"
    assert config.mcp_search_index == "content_vector"
    assert config.mcp_mapping == {"search": "custom_search", "search_params": {"min_score": 0.4}}
    assert config.mcp_bundles == ("news", "standard_page")
    assert config.mcp_extra_tools == ("tool_api__canvas_list_pages",)


@pytest.mark.parametrize("value", ["{not json", '["a list"]'])
def test_a_mapping_that_isnt_a_json_object_is_ignored(monkeypatch, caplog, value) -> None:
    monkeypatch.setenv("DREACHY_MCP_MAPPING", value)

    with caplog.at_level(logging.WARNING):
        assert Config.from_env().mcp_mapping == {}

    assert "DREACHY_MCP_MAPPING" in caplog.text


def test_an_mcp_endpoint_on_another_host_is_ignored(monkeypatch, caplog) -> None:
    # The site's access token rides on every MCP request: it goes to the site, nowhere else.
    monkeypatch.setenv("DREACHY_MCP_ENDPOINT", "https://elsewhere.example/mcp")

    with caplog.at_level(logging.WARNING):
        config = Config.from_env()

    assert config.effective_mcp_endpoint == "https://site.test/mcp"
    assert "elsewhere.example" in caplog.text


def test_mcp_without_the_site_login_warns(monkeypatch, caplog) -> None:
    monkeypatch.setenv("DREACHY_BACKEND", "mcp")

    with caplog.at_level(logging.WARNING):
        Config.from_env()

    assert "login" in caplog.text
