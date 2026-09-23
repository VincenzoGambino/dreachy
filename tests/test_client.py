"""Mocked-HTTP unit tests for DrupalClient. No live site, no robot."""

from __future__ import annotations

import re

import httpx
import pytest

from dreachy.client import DreachySiteError, DrupalClient
from dreachy.config import Config

# ---------------------------------------------------------------------------
# Fixture data — shaped like the real Umami site (confirmed 2026-07-27):
# article/page use field_body (no summary field); recipe uses
# field_recipe_instruction + a separate field_summary.
# ---------------------------------------------------------------------------


def _node_resource(*, uuid: str, bundle: str, title: str, created: str, path: str, **text_fields):
    return {
        "type": f"node--{bundle}",
        "id": uuid,
        "attributes": {
            "title": title,
            "created": created,
            "changed": created,
            "path": {"alias": path, "pid": 1, "langcode": "en"},
            **text_fields,
        },
    }


ARTICLES = [
    _node_resource(
        uuid="a1-uuid",
        bundle="article",
        title="Give your oatmeal the ultimate makeover",
        created="2026-07-20T10:00:00+00:00",
        path="/articles/oatmeal-makeover",
        field_body={"value": "<p>Oatmeal is <strong>great</strong> for breakfast.</p>"},
    ),
    _node_resource(
        uuid="a2-uuid",
        bundle="article",
        title="Spring salad ideas",
        created="2026-07-22T09:00:00+00:00",
        path="/articles/spring-salad",
        field_body={"value": "<p>Fresh salads for spring.</p>"},
    ),
]

PAGES = [
    _node_resource(
        uuid="p1-uuid",
        bundle="page",
        title="About Umami",
        created="2026-07-15T08:00:00+00:00",
        path="/about-umami",
        field_body={"value": "<p>Umami is a fictional food magazine.</p>"},
    ),
]

RECIPES = [
    _node_resource(
        uuid="r1-uuid",
        bundle="recipe",
        title="Borscht with pork ribs",
        created="2026-07-25T12:00:00+00:00",
        path="/recipes/borscht-with-pork-ribs",
        field_recipe_instruction={"value": "<ol><li>Cook the ribs.</li></ol>"},
        field_summary={"value": "A hearty Ukrainian soup."},
    ),
]

_DATASETS = {
    "/en/jsonapi/node/article": ARTICLES,
    "/en/jsonapi/node/page": PAGES,
    "/en/jsonapi/node/recipe": RECIPES,
}

# path alias -> (bundle, uuid), mirrors what Decoupled Router resolves on the real site
PATH_TO_ENTITY = {
    "/articles/oatmeal-makeover": ("article", "a1-uuid"),
    "/recipes/borscht-with-pork-ribs": ("recipe", "r1-uuid"),
}

_RESOURCE_URL_RE = re.compile(r"^/en/jsonapi/node/(article|page|recipe)/([\w-]+)$")


def _json_response(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _find_resource(bundle: str, uuid: str) -> dict | None:
    for resource in _DATASETS.get(f"/en/jsonapi/node/{bundle}", []):
        if resource["id"] == uuid:
            return resource
    return None


def _default_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    params = request.url.params

    if path == "/en/router/translate-path":
        alias = params.get("path")
        entity = PATH_TO_ENTITY.get(alias)
        if entity is None:
            return _json_response({"resolved": False, "message": f"Unable to resolve {alias!r}."})
        bundle, uuid = entity
        return _json_response(
            {"resolved": True, "isHomePath": False, "entity": {"type": "node", "bundle": bundle, "uuid": uuid}}
        )

    match = _RESOURCE_URL_RE.match(path)
    if match:
        bundle, uuid = match.groups()
        resource = _find_resource(bundle, uuid)
        if resource is None:
            return _json_response({"errors": [{"detail": "not found"}]}, status=404)
        return _json_response({"data": resource})

    dataset = _DATASETS.get(path)
    if dataset is not None:
        contains_kw = params.get("filter[title][value]")
        equal_kw = params.get("filter[title]")
        if contains_kw:
            dataset = [r for r in dataset if contains_kw.lower() in r["attributes"]["title"].lower()]
        elif equal_kw:
            dataset = [r for r in dataset if r["attributes"]["title"] == equal_kw]
        return _json_response({"jsonapi": {"version": "1.1"}, "data": dataset, "links": {}})

    return _json_response({"errors": [{"detail": f"unhandled path {path}"}]}, status=404)


def _make_client(handler=_default_handler) -> DrupalClient:
    transport = httpx.MockTransport(handler)
    return DrupalClient(Config(), http_client=httpx.Client(transport=transport))


# ---------------------------------------------------------------------------
# get_recent_nodes
# ---------------------------------------------------------------------------


def test_get_recent_nodes_merges_and_sorts_across_content_types() -> None:
    with _make_client() as client:
        nodes = client.get_recent_nodes(limit=10)

    assert [n["type"] for n in nodes] == ["recipe", "article", "article", "page"]
    assert [n["id"] for n in nodes] == ["r1-uuid", "a2-uuid", "a1-uuid", "p1-uuid"]


def test_get_recent_nodes_shapes_article_fields_with_derived_summary() -> None:
    with _make_client() as client:
        nodes = client.get_recent_nodes(limit=10)

    article = next(n for n in nodes if n["id"] == "a1-uuid")
    assert article["title"] == "Give your oatmeal the ultimate makeover"
    assert article["path"] == "/articles/oatmeal-makeover"
    assert article["body"] == "Oatmeal is great for breakfast."
    # article has no distinct teaser field — summary falls back to (truncated) body
    assert article["summary"] == article["body"]


def test_get_recent_nodes_shapes_recipe_fields_with_own_summary_field() -> None:
    with _make_client() as client:
        nodes = client.get_recent_nodes(limit=10)

    recipe = next(n for n in nodes if n["id"] == "r1-uuid")
    assert recipe["body"] == "Cook the ribs."
    assert recipe["summary"] == "A hearty Ukrainian soup."
    assert recipe["summary"] != recipe["body"]


def test_get_recent_nodes_respects_limit() -> None:
    with _make_client() as client:
        nodes = client.get_recent_nodes(limit=2)

    assert len(nodes) == 2
    assert [n["id"] for n in nodes] == ["r1-uuid", "a2-uuid"]


# ---------------------------------------------------------------------------
# find_content
# ---------------------------------------------------------------------------


def test_find_content_filters_by_keyword_across_types() -> None:
    with _make_client() as client:
        matches = client.find_content("oatmeal")

    assert [m["id"] for m in matches] == ["a1-uuid"]


def test_find_content_restricts_to_given_content_type() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return _default_handler(request)

    with _make_client(handler) as client:
        matches = client.find_content("pork", content_type="recipe")

    assert [m["id"] for m in matches] == ["r1-uuid"]
    assert requested_paths == ["/en/jsonapi/node/recipe"]


# ---------------------------------------------------------------------------
# get_site_pulse
# ---------------------------------------------------------------------------


def test_get_site_pulse_returns_counts_and_latest_timestamps() -> None:
    with _make_client() as client:
        pulse = client.get_site_pulse()

    assert pulse["node_count"] == 4
    assert pulse["latest_node_created"] == "2026-07-25T12:00:00+00:00"


# ---------------------------------------------------------------------------
# get_article
# ---------------------------------------------------------------------------


def test_get_article_resolves_by_path() -> None:
    with _make_client() as client:
        article = client.get_article("/articles/oatmeal-makeover")

    assert article is not None
    assert article["id"] == "a1-uuid"
    assert article["type"] == "article"


def test_get_article_adds_leading_slash_if_missing() -> None:
    with _make_client() as client:
        article = client.get_article("articles/oatmeal-makeover")

    assert article is not None
    assert article["id"] == "a1-uuid"


def test_get_article_falls_back_to_title_search_when_path_not_resolved() -> None:
    with _make_client() as client:
        article = client.get_article("About Umami")

    assert article is not None
    assert article["id"] == "p1-uuid"
    assert article["type"] == "page"


def test_get_article_returns_none_when_nothing_matches() -> None:
    with _make_client() as client:
        article = client.get_article("Nonexistent Title")

    assert article is None


# ---------------------------------------------------------------------------
# Error boundary
# ---------------------------------------------------------------------------


def test_http_status_error_raises_dreachy_site_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"errors": [{"detail": "boom"}]}, status=500)

    with _make_client(handler) as client:
        with pytest.raises(DreachySiteError):
            client.get_recent_nodes()


def test_connection_error_raises_dreachy_site_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    with _make_client(handler) as client:
        with pytest.raises(DreachySiteError):
            client.get_recent_nodes()


# ---------------------------------------------------------------------------
# Language prefix: multilingual sites serve JSON:API under /<langcode>/, but a
# single-language site has no prefix at all and 404s on /en/jsonapi/...
# ---------------------------------------------------------------------------


def _record_paths(config: Config) -> list[str]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"data": []})

    client = DrupalClient(config, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    client.get_recent_nodes(limit=1)
    return seen


def test_client_omits_the_language_prefix_when_no_locale_is_set() -> None:
    seen = _record_paths(Config(default_locale=None))

    assert seen, "expected at least one request"
    assert all(path.startswith("/jsonapi/") for path in seen), seen


def test_client_uses_the_language_prefix_when_a_locale_is_set() -> None:
    seen = _record_paths(Config(default_locale="en"))

    assert seen, "expected at least one request"
    assert all(path.startswith("/en/jsonapi/") for path in seen), seen
