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
