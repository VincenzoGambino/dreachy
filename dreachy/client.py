"""DrupalClient — thin sync wrapper over drupal-api-client's JsonApiClient.

Returns plain dicts only; no drupal_api_client types leak past this module.
Sync-only (per drupal-api-client) — tools call this via asyncio.to_thread().

Field-name notes (confirmed against the live Umami demo site, 2026-07-27):
article and page share a plain ``field_body`` with no distinct teaser field;
recipe has no ``field_body`` at all — its full text is ``field_recipe_
instruction`` and its teaser is a genuinely separate ``field_summary``. See
``_TEXT_FIELDS`` below. All of these render as HTML (even the "processed"
variant), so every text extraction goes through ``_strip_html``.
"""

from __future__ import annotations

import re
from types import TracebackType
from typing import Any

import httpx
from drupal_api_client import JsonApiClient, ResourceNotFoundError
from drupal_jsonapi_params import DrupalJsonApiParams
from drupal_jsonapi_params.operators import FilterOperator

from .config import Config

_TAG_RE = re.compile(r"<[^>]+>")
_SUMMARY_FALLBACK_CHARS = 200

_TEXT_FIELDS: dict[str, dict[str, str | None]] = {
    "article": {"body": "field_body", "summary": None},
    "page": {"body": "field_body", "summary": None},
    "recipe": {"body": "field_recipe_instruction", "summary": "field_summary"},
}


class DreachySiteError(Exception):
    """The Drupal site is unreachable or returned an error response."""


def _strip_html(text: str) -> str:
    """Collapse Drupal's rendered HTML into plain, speakable text."""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text)).strip()


def _text_field(attributes: dict[str, Any], field_name: str | None) -> str:
    if not field_name:
        return ""
    field = attributes.get(field_name)
    if not field:
        return ""
    return _strip_html(field.get("processed") or field.get("value") or "")


def _node_to_dict(bundle: str, resource: dict[str, Any]) -> dict[str, Any]:
    attributes = resource["attributes"]
    fields = _TEXT_FIELDS.get(bundle, {"body": None, "summary": None})
    body = _text_field(attributes, fields["body"])
    summary = _text_field(attributes, fields["summary"]) or body[:_SUMMARY_FALLBACK_CHARS]
    path = attributes.get("path") or {}
    return {
        "id": resource["id"],
        "title": attributes.get("title"),
        "type": bundle,
        "created": attributes.get("created"),
        "changed": attributes.get("changed"),
        "path": path.get("alias"),
        "body": body,
        "summary": summary,
    }


class DrupalClient:
    """Sync wrapper over JsonApiClient exposing Dreachy's query behaviours."""

    def __init__(self, config: Config, *, http_client: httpx.Client | None = None) -> None:
        self.config = config
        # No cache: InMemoryCache has no TTL, and every read below passes
        # disable_cache=True — a long-lived client (e.g. the tools' shared
        # singleton) would otherwise never see content published after its
        # first query of a given shape, which silently broke the watcher.
        self._client = JsonApiClient(
            config.base_url,
            timeout=config.request_timeout,
            default_locale=config.default_locale,
            http_client=http_client,
        )

    def __enter__(self) -> DrupalClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _get_collection(self, resource_type: str, params: DrupalJsonApiParams) -> list[dict[str, Any]]:
        try:
            response = self._client.get_collection(
                resource_type, query_string=params, raise_for_status=True, disable_cache=True
            )
        except httpx.HTTPError as exc:
            raise DreachySiteError(str(exc)) from exc
        return response["data"]

    def get_recent_nodes(self, limit: int | None = None) -> list[dict[str, Any]]:
        limit = limit or self.config.whats_new_limit
        nodes: list[dict[str, Any]] = []
        for bundle in self.config.content_types:
            params = DrupalJsonApiParams().add_sort("created", "DESC").add_page_limit(limit)
            for resource in self._get_collection(f"node--{bundle}", params):
                nodes.append(_node_to_dict(bundle, resource))
        nodes.sort(key=lambda n: n["created"], reverse=True)
        return nodes[:limit]

    def find_content(
        self, keyword: str, content_type: str | None = None
    ) -> list[dict[str, Any]]:
        limit = self.config.find_content_limit
        bundles = (content_type,) if content_type else self.config.content_types
        matches: list[dict[str, Any]] = []
        for bundle in bundles:
            params = (
                DrupalJsonApiParams()
                .add_filter("title", keyword, operator=FilterOperator.CONTAINS)
                .add_sort("created", "DESC")
                .add_page_limit(limit)
            )
            for resource in self._get_collection(f"node--{bundle}", params):
                matches.append(_node_to_dict(bundle, resource))
        matches.sort(key=lambda n: n["created"], reverse=True)
        return matches[:limit]

    def get_article(self, title_or_path: str) -> dict[str, Any] | None:
        path = title_or_path if title_or_path.startswith("/") else f"/{title_or_path}"
        resource = None
        try:
            resource = self._client.get_resource_by_path(path, raise_for_status=True, disable_cache=True)
        except ResourceNotFoundError:
            pass
        except httpx.HTTPError as exc:
            raise DreachySiteError(str(exc)) from exc

        if resource is not None:
            bundle = resource["data"]["type"].split("--")[1]
            return _node_to_dict(bundle, resource["data"])

        for bundle in self.config.content_types:
            params = (
                DrupalJsonApiParams()
                .add_filter("title", title_or_path, operator=FilterOperator.EQUAL)
                .add_page_limit(1)
            )
            found = self._get_collection(f"node--{bundle}", params)
            if found:
                return _node_to_dict(bundle, found[0])
        return None

    def get_site_pulse(self) -> dict[str, Any]:
        """Node counts and latest activity timestamp.

        The count is capped at ``config.pulse_sample_limit`` — core JSON:API
        doesn't expose a cheap collection total, so this is an explicit
        approximation suited to a small demo site, not a true count.
        """
        nodes = self.get_recent_nodes(limit=self.config.pulse_sample_limit)
        return {
            "node_count": len(nodes),
            "latest_node_created": nodes[0]["created"] if nodes else None,
        }
