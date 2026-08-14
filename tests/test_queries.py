"""Mocked-HTTP unit tests for tool_queries.py's Q&A shaping logic.

Self-contained fixtures, deliberately not shared with test_client.py: these
tests exercise shaping (titles/ages/teasers), not filtering or path-resolution
mechanics, which test_client.py already covers thoroughly. No live site, no
robot.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dreachy.client import DreachySiteError, DrupalClient
from dreachy.config import Config
from dreachy.tool_queries import (
    drupal_find_content,
    drupal_read_article,
    drupal_site_pulse,
    drupal_whats_new,
)


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _node_resource(*, uuid: str, bundle: str, title: str, created: str, **text_fields) -> dict:
    return {
        "type": f"node--{bundle}",
        "id": uuid,
        "attributes": {
            "title": title,
            "created": created,
            "changed": created,
            "path": {"alias": f"/n/{uuid}", "pid": 1, "langcode": "en"},
            **text_fields,
        },
    }


def _json_response(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _build_handler(datasets: dict[str, list[dict]]):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/en/router/translate-path":
            return _json_response({"resolved": False, "message": "not a path alias"})

        dataset = datasets.get(path)
        if dataset is not None:
            equal_kw = request.url.params.get("filter[title]")
            if equal_kw:
                dataset = [r for r in dataset if r["attributes"]["title"] == equal_kw]
            return _json_response({"jsonapi": {"version": "1.1"}, "data": dataset, "links": {}})

        return _json_response({"errors": [{"detail": f"unhandled {path}"}]}, status=404)

    return handler


def _make_client(datasets: dict[str, list[dict]]) -> DrupalClient:
    transport = httpx.MockTransport(_build_handler(datasets))
    return DrupalClient(Config(), http_client=httpx.Client(transport=transport))


_EMPTY_DATASETS = {
    "/en/jsonapi/node/article": [],
    "/en/jsonapi/node/page": [],
    "/en/jsonapi/node/recipe": [],
}


# ---------------------------------------------------------------------------
# drupal_whats_new
# ---------------------------------------------------------------------------


def test_drupal_whats_new_shapes_items_with_humanized_age() -> None:
    datasets = {
        **_EMPTY_DATASETS,
        "/en/jsonapi/node/article": [
            _node_resource(
                uuid="a-today", bundle="article", title="Fresh off the press",
                created=_iso(0), field_body={"value": "..."},
            ),
            _node_resource(
                uuid="a-yesterday", bundle="article", title="Yesterday's news",
                created=_iso(1), field_body={"value": "..."},
            ),
            _node_resource(
                uuid="a-week", bundle="article", title="Last week's story",
                created=_iso(10), field_body={"value": "..."},
            ),
        ],
        "/en/jsonapi/node/recipe": [
            _node_resource(
                uuid="r-old", bundle="recipe", title="An old favourite", created=_iso(40),
                field_recipe_instruction={"value": "..."}, field_summary={"value": "..."},
            ),
        ],
    }

    with _make_client(datasets) as client:
        result = drupal_whats_new(client, limit=10)

    ages_by_title = {item["title"]: item["age"] for item in result["items"]}
    assert ages_by_title["Fresh off the press"] == "today"
    assert ages_by_title["Yesterday's news"] == "yesterday"
    assert ages_by_title["Last week's story"] == "1 week ago"
    assert ages_by_title["An old favourite"] == "1 month ago"

    types_by_title = {item["title"]: item["type"] for item in result["items"]}
    assert types_by_title["An old favourite"] == "recipe"


def test_drupal_whats_new_propagates_site_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"errors": [{"detail": "boom"}]}, status=500)

    transport = httpx.MockTransport(handler)
    with DrupalClient(Config(), http_client=httpx.Client(transport=transport)) as client:
        with pytest.raises(DreachySiteError):
            drupal_whats_new(client)


# ---------------------------------------------------------------------------
# drupal_find_content
# ---------------------------------------------------------------------------


def test_drupal_find_content_shapes_matches_with_teaser() -> None:
    datasets = {
        **_EMPTY_DATASETS,
        "/en/jsonapi/node/article": [
            _node_resource(
                uuid="a1", bundle="article", title="Sourdough starter tips", created=_iso(2),
                field_body={"value": "<p>Keep it warm and fed.</p>"},
            ),
        ],
    }

    with _make_client(datasets) as client:
        result = drupal_find_content(client, keyword="sourdough")

    assert result["matches"] == [
        {"title": "Sourdough starter tips", "type": "article", "teaser": "Keep it warm and fed."}
    ]


def test_drupal_find_content_returns_empty_matches_not_an_error() -> None:
    with _make_client(_EMPTY_DATASETS) as client:
        result = drupal_find_content(client, keyword="nothing matches this")

    assert result == {"matches": []}


# ---------------------------------------------------------------------------
# drupal_site_pulse
# ---------------------------------------------------------------------------


def test_drupal_site_pulse_shapes_count_and_latest_activity_age() -> None:
    datasets = {
        **_EMPTY_DATASETS,
        "/en/jsonapi/node/article": [
            _node_resource(uuid="a1", bundle="article", title="One", created=_iso(3), field_body={"value": "x"}),
            _node_resource(uuid="a2", bundle="article", title="Two", created=_iso(1), field_body={"value": "x"}),
        ],
    }

    with _make_client(datasets) as client:
        result = drupal_site_pulse(client)

    assert result == {"node_count": 2, "latest_activity_age": "yesterday"}


def test_drupal_site_pulse_latest_activity_age_is_none_when_no_nodes() -> None:
    with _make_client(_EMPTY_DATASETS) as client:
        result = drupal_site_pulse(client)

    assert result == {"node_count": 0, "latest_activity_age": None}


# ---------------------------------------------------------------------------
# drupal_read_article
# ---------------------------------------------------------------------------


def test_drupal_read_article_shapes_title_and_text_on_match() -> None:
    datasets = {
        **_EMPTY_DATASETS,
        "/en/jsonapi/node/page": [
            _node_resource(
                uuid="p1", bundle="page", title="About Umami", created=_iso(5),
                field_body={"value": "<p>Umami is a fictional food magazine.</p>"},
            ),
        ],
    }

    with _make_client(datasets) as client:
        result = drupal_read_article(client, title_or_path="About Umami")

    assert result == {"title": "About Umami", "text": "Umami is a fictional food magazine."}


def test_drupal_read_article_returns_none_when_nothing_matches() -> None:
    with _make_client(_EMPTY_DATASETS) as client:
        result = drupal_read_article(client, title_or_path="Nonexistent")

    assert result is None
