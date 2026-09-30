"""DrupalClient — thin sync wrapper over drupal-api-client's JsonApiClient.

Returns plain dicts only; no drupal_api_client types leak past this module.
Sync-only (per drupal-api-client) — tools call this via asyncio.to_thread().

Which node types to query, and which of their fields hold the text, comes
from the site itself: get_schema() discovers it (schema.py documents the
route and heuristics). Until a discovery succeeds the client uses
schema.FALLBACK_TYPES, the Umami mapping confirmed against the live demo
site 2026-07-27. Text fields render as HTML (even the "processed"
variant), so every text extraction goes through ``_strip_html``.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from types import TracebackType
from typing import Any
from urllib.parse import urljoin

import httpx
from drupal_api_client import JsonApiClient, ResourceNotFoundError
from drupal_jsonapi_params import DrupalJsonApiParams
from drupal_jsonapi_params.operators import FilterOperator

from .config import Config
from .schema import Schema, TypeSchema, build_schema, fallback_schema, select_types

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_SUMMARY_FALLBACK_CHARS = 200
# Statuses that mean "anonymous may not read this type", as opposed to a
# temporary failure: only these let discovery skip a type and carry on.
_UNREADABLE_STATUSES = (401, 403, 404)


class DreachySiteError(Exception):
    """The Drupal site is unreachable or returned an error response."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        # The HTTP status, when the site answered with an error one.
        self.status = status


def _strip_html(text: str) -> str:
    """Collapse Drupal's rendered HTML into plain, speakable text."""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text)).strip()


def _text_field(attributes: dict[str, Any], field_name: str | None) -> str:
    if not field_name:
        return ""
    field = attributes.get(field_name)
    if not isinstance(field, dict):
        return ""
    return _strip_html(field.get("processed") or field.get("value") or "")


def _node_to_dict(bundle: str, type_schema: TypeSchema, resource: dict[str, Any]) -> dict[str, Any]:
    attributes = resource["attributes"]
    body = next((text for name in type_schema.text_fields if (text := _text_field(attributes, name))), "")
    summary = _text_field(attributes, type_schema.summary_field) or body[:_SUMMARY_FALLBACK_CHARS]
    path = attributes.get("path") or {}
    return {
        "id": resource["id"],
        "title": attributes.get(type_schema.label_field),
        "type": bundle,
        "created": attributes.get("created"),
        "changed": attributes.get("changed"),
        "path": path.get("alias"),
        "body": body,
        "summary": summary,
    }


class DrupalClient:
    """Sync wrapper over JsonApiClient exposing Dreachy's query behaviours."""

    def __init__(
        self, config: Config, *, http_client: httpx.Client | None = None, auto_discover: bool = False
    ) -> None:
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
        # auto_discover: queries retry a failed discovery (see _types). Off
        # by default, so a client built for a test, or before a site URL is
        # configured, never sends discovery requests of its own accord.
        self._auto_discover = auto_discover
        self._full_schema: Schema = fallback_schema(config.content_types)
        self.schema_discovered = False
        self._next_discovery_at = 0.0
        self._discovery_lock = threading.Lock()

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
            return response["data"]
        except httpx.HTTPStatusError as exc:
            raise DreachySiteError(str(exc), status=exc.response.status_code) from exc
        except httpx.HTTPError as exc:
            raise DreachySiteError(str(exc)) from exc
        except (ValueError, KeyError, TypeError) as exc:
            # A 200 that isn't JSON:API (a captive portal, a maintenance page).
            # Left raw, it would end the watcher's task and bypass discovery's
            # retry throttle.
            raise DreachySiteError(f"Unexpected response for {resource_type}: {exc!r}") from exc

    # -- content model --------------------------------------------------

    @property
    def full_schema(self) -> Schema:
        """Every type Dreachy knows of, before the installer's selection."""
        return dict(self._full_schema)

    @property
    def schema(self) -> Schema:
        """The types Dreachy talks about. Never touches the network."""
        return select_types(self._full_schema, self.config.enabled_types)

    def get_schema(self) -> Schema:
        """Discover the site's node types and their text fields.

        Raises DreachySiteError when the JSON:API index can't be read.
        """
        bundles = self._node_bundles()
        labels = self._node_type_labels()
        samples = {bundle: self._sample_attributes(bundle) for bundle in bundles}
        return build_schema(bundles, labels, samples)

    def refresh_schema(self) -> bool:
        """Replace the cached schema with a fresh discovery.

        On failure keeps the current schema (the fallback, or the last one
        discovered) and returns False; never raises DreachySiteError.
        """
        with self._discovery_lock:
            return self._refresh_locked()

    def _refresh_locked(self) -> bool:
        try:
            schema = self.get_schema()
        except DreachySiteError as exc:
            return self._discovery_failed(str(exc))
        if not schema:
            return self._discovery_failed("no readable content type has text fields")
        self._full_schema = schema
        self.schema_discovered = True
        return True

    def _discovery_failed(self, reason: str) -> bool:
        logger.warning(
            "Couldn't discover the site's content types (%s); using %s", reason, ", ".join(self._full_schema)
        )
        self._next_discovery_at = time.monotonic() + self.config.schema_retry_seconds
        return False

    def _discovery_due(self) -> bool:
        return not self.schema_discovered and time.monotonic() >= self._next_discovery_at

    def _types(self) -> Schema:
        """The enabled schema, first retrying a failed discovery if one is due."""
        if self._auto_discover and self._discovery_due():
            with self._discovery_lock:
                # Checked again under the lock: callers that queued behind
                # another thread's discovery use its result, rather than
                # each running their own back to back.
                if self._discovery_due():
                    self._refresh_locked()
        return self.schema

    def _index_url(self) -> str:
        locale = self.config.default_locale
        return urljoin(self._client.base_url, f"{locale}/jsonapi" if locale else "jsonapi")

    def _node_bundles(self) -> list[str]:
        try:
            response = self._client.fetch(self._index_url(), raise_for_status=True)
            links = response.json().get("links", {})
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            raise DreachySiteError(f"JSON:API index unavailable: {exc}") from exc
        return [key.split("--", 1)[1] for key in links if key.startswith("node--")]

    def _node_type_labels(self) -> dict[str, str]:
        """Human labels, best effort: anonymous may not be allowed to view node types."""
        try:
            resources = self._get_collection("node_type--node_type", DrupalJsonApiParams())
        except DreachySiteError as exc:
            logger.info("Node type labels unavailable, using machine names: %s", exc)
            return {}
        labels: dict[str, str] = {}
        for resource in resources:
            attributes = resource.get("attributes") or {}
            if attributes.get("drupal_internal__type") and attributes.get("name"):
                labels[attributes["drupal_internal__type"]] = attributes["name"]
        return labels

    def _sample_attributes(self, bundle: str) -> list[dict[str, Any]] | None:
        """Recent nodes' attributes, or None if this type can't be read at all."""
        params = DrupalJsonApiParams().add_sort("created", "DESC").add_page_limit(self.config.schema_sample_size)
        try:
            resources = self._get_collection(f"node--{bundle}", params)
        except DreachySiteError as exc:
            # One unreadable type (e.g. no anonymous view access) mustn't sink
            # the rest. Anything else — a 5xx, a timeout — fails the whole
            # discovery, so the retry picks the type up rather than losing it
            # for the session.
            if exc.status not in _UNREADABLE_STATUSES:
                raise
            logger.info("Can't sample node--%s, skipping it: %s", bundle, exc)
            return None
        return [resource.get("attributes") or {} for resource in resources]

    # -- queries --------------------------------------------------------

    def get_recent_nodes(self, limit: int | None = None) -> list[dict[str, Any]]:
        limit = limit or self.config.whats_new_limit
        nodes: list[dict[str, Any]] = []
        for bundle, type_schema in self._types().items():
            params = DrupalJsonApiParams().add_sort("created", "DESC").add_page_limit(limit)
            for resource in self._get_collection(f"node--{bundle}", params):
                nodes.append(_node_to_dict(bundle, type_schema, resource))
        nodes.sort(key=lambda n: n["created"], reverse=True)
        return nodes[:limit]

    def find_content(
        self, keyword: str, content_type: str | None = None
    ) -> list[dict[str, Any]]:
        limit = self.config.find_content_limit
        types = self._types()
        # An unknown or disabled type (e.g. from a tool spec built before a
        # settings change) searches every enabled type rather than finding
        # nothing.
        if content_type in types:
            types = {content_type: types[content_type]}
        matches: list[dict[str, Any]] = []
        for bundle, type_schema in types.items():
            params = (
                DrupalJsonApiParams()
                .add_filter(type_schema.label_field, keyword, operator=FilterOperator.CONTAINS)
                .add_sort("created", "DESC")
                .add_page_limit(limit)
            )
            for resource in self._get_collection(f"node--{bundle}", params):
                matches.append(_node_to_dict(bundle, type_schema, resource))
        matches.sort(key=lambda n: n["created"], reverse=True)
        return matches[:limit]

    def get_article(self, title_or_path: str) -> dict[str, Any] | None:
        types = self._types()
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
            if bundle in types:
                return _node_to_dict(bundle, types[bundle], resource["data"])
            # A path to something Dreachy doesn't talk about (a disabled
            # type, a taxonomy term): fall through to the title search.

        for bundle, type_schema in types.items():
            params = (
                DrupalJsonApiParams()
                .add_filter(type_schema.label_field, title_or_path, operator=FilterOperator.EQUAL)
                .add_page_limit(1)
            )
            found = self._get_collection(f"node--{bundle}", params)
            if found:
                return _node_to_dict(bundle, type_schema, found[0])
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
