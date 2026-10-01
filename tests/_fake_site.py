"""Fixture sites for content-model discovery tests.

Imported as a plain module (pytest's default import mode puts tests/ on
sys.path). Shaped like core JSON:API output: formatted text fields are
objects with value/format/processed; plain strings, numbers, lists and link
objects sit alongside them and must not be mistaken for text.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from dreachy.client import DrupalClient
from dreachy.config import Config


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


# A small newsroom: news_item is moderated (its content carries a
# moderation_state), event isn't. Unpublished work in several states,
# including archived content, which isn't "pending".
EDITORIAL_LABELS = {"news_item": "News item", "event": "Event"}
EDITORIAL_NODES = {
    "news_item": [
        node("news_item", "n1", "Council approves new park", "2026-09-28T09:00:00+00:00",
             body=formatted("<p>The council voted.</p>"), moderation_state="published"),
        node("news_item", "d1", "Park opening hours", "2026-09-29T09:00:00+00:00",
             status=False, body=formatted("<p>Draft.</p>"), moderation_state="draft"),
        node("news_item", "r1", "Budget report", "2026-09-27T09:00:00+00:00",
             status=False, body=formatted("<p>In review.</p>"), moderation_state="review"),
        node("news_item", "a1", "Old fair", "2026-01-01T09:00:00+00:00",
             status=False, body=formatted("<p>Gone.</p>"), moderation_state="archived"),
    ],
    "event": [
        node("event", "e1", "Harvest fair", "2026-09-25T09:00:00+00:00",
             field_description=formatted("<p>Stalls.</p>")),
        node("event", "e2", "Winter market", "2026-09-26T09:00:00+00:00",
             status=False, field_description=formatted("<p>Plans.</p>")),
    ],
}


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
        errors: dict[str, int] | None = None,
        write_status: int | None = None,
        publish_on_create: bool = False,
        write_detail: str | None = None,
        filter_hides_drafts: bool = False,
    ) -> None:
        self.nodes = nodes
        self.labels = labels or {}
        self.locale = locale
        self.index_status = index_status
        self.node_types_status = node_types_status
        self.unreadable = unreadable
        # Per-type status for collection requests, e.g. {"recipe": 502}.
        self.errors = errors or {}
        # Writes (R3): a forced error status, or a site that publishes regardless.
        self.write_status = write_status
        self.publish_on_create = publish_on_create
        self.created: list[dict[str, Any]] = []
        # The JSON:API errors[].detail sent with write_status.
        self.write_detail = write_detail
        # Core JSON:API filter access: without `bypass node access`, a *filtered*
        # node collection admits only published content (JsonapiHooks::
        # jsonapiNodeFilterAccess), whatever else the account may view per item.
        self.filter_hides_drafts = filter_hides_drafts
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
        if request.method == "POST" and (match := re.fullmatch(rf"{re.escape(self._api)}/node/(\w+)", path)):
            return self._create(match.group(1), request)
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
        if bundle in self.errors:
            return _error(self.errors[bundle])
        if bundle not in self.nodes:
            return _error(404)
        data = sorted(self.nodes[bundle], key=lambda r: r["attributes"]["created"], reverse=True)
        if self.filter_hides_drafts and any(key.startswith("filter[") for key in params.keys()):
            data = [r for r in data if r["attributes"].get("status", True)]
        if params.get("filter[status]") == "1":
            data = [r for r in data if r["attributes"].get("status", True)]
        elif params.get("filter[status]") == "0":
            data = [r for r in data if not r["attributes"].get("status", True)]
        if contains := params.get("filter[title][value]"):
            data = [r for r in data if contains.lower() in r["attributes"]["title"].lower()]
        elif equal := params.get("filter[title]"):
            data = [r for r in data if r["attributes"]["title"] == equal]
        if limit := params.get("page[limit]"):
            data = data[: int(limit)]
        return httpx.Response(200, json={"data": data, "links": {}})

    def _create(self, bundle: str, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.created.append(body)
        if self.write_status is not None:
            if self.write_detail is not None:
                error = {"status": str(self.write_status), "detail": self.write_detail}
                return httpx.Response(self.write_status, json={"errors": [error]})
            return _error(self.write_status)
        attributes = dict(body["data"]["attributes"])
        title = attributes.pop("title")
        resource = node(bundle, f"new-{len(self.created)}", title, "2026-09-30T12:00:00+00:00", **attributes)
        resource["attributes"]["status"] = self.publish_on_create
        return httpx.Response(201, json={"data": resource})

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
