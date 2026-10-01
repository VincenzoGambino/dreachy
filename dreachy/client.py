"""JsonApiBackend — the Backend (backend.py) over Drupal's JSON:API, via drupal-api-client's JsonApiClient.

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
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

import httpx
from drupal_api_client import AuthenticationError, JsonApiClient, ResourceNotFoundError
from drupal_jsonapi_params import DrupalJsonApiParams
from drupal_jsonapi_params.operators import FilterOperator

from .auth import library_authentication
from .backend import Backend, DreachyAuthError, DreachySiteError
from .config import Config
from .schema import Schema, TypeSchema, build_schema

logger = logging.getLogger(__name__)

__all__ = ["DreachySiteError", "DrupalClient", "JsonApiBackend"]

_TAG_RE = re.compile(r"<[^>]+>")
_SUMMARY_FALLBACK_CHARS = 200
# Statuses that mean "anonymous may not read this type", as opposed to a
# temporary failure: only these let discovery skip a type and carry on.
_UNREADABLE_STATUSES = (401, 403, 404)
# Never includes credentials; the tools prefix it with "I can't reach the site right now: ".
# Hedged on purpose: drupal-api-client raises AuthenticationError for any
# failed grant, a 503 from a site in maintenance mode included.
_CREDENTIALS_REFUSED = (
    "couldn't log in to the site: it refused Dreachy's credentials or is unavailable"
    " — if this keeps happening, check the site login on Dreachy's settings page"
)

# Core's fallback format: usable by every role, so notes need no format permission.
_NOTE_TEXT_FORMAT = "plain_text"
_DRAFT_STATE = "draft"
_ARCHIVED_STATE = "archived"


def _error_detail(response: httpx.Response) -> str:
    """The first JSON:API error's detail, shortened; "" if there isn't one."""
    try:
        errors = response.json().get("errors") or []
        detail = str(errors[0].get("detail") or "") if errors else ""
    except (ValueError, AttributeError, TypeError):
        return ""
    return detail[:200]


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
        "status": bool(attributes.get("status", True)),
        "moderation_state": attributes.get("moderation_state"),
    }


def _published(params: DrupalJsonApiParams, include_unpublished: bool = False) -> DrupalJsonApiParams:
    """Published content only, unless a caller explicitly opts in.

    Anonymous access sees only published nodes anyway; a logged-in Dreachy
    (R2) may be able to view drafts, and no tool may read them aloud, search
    them or react to them. R3's editorial tools opt in deliberately.
    """
    return params if include_unpublished else params.add_filter("status", 1)


class JsonApiBackend(Backend):
    """The Backend over JSON:API: anonymous by default."""

    def __init__(
        self, config: Config, *, http_client: httpx.Client | None = None, auto_discover: bool = False
    ) -> None:
        super().__init__(config, auto_discover=auto_discover)
        # No cache: InMemoryCache has no TTL, and every read below passes
        # disable_cache=True — a long-lived client (e.g. the tools' shared
        # singleton) would otherwise never see content published after its
        # first query of a given shape, which silently broke the watcher.
        self._client = JsonApiClient(
            config.base_url,
            timeout=config.request_timeout,
            default_locale=config.default_locale,
            http_client=http_client,
            # None = anonymous; the refresh margin and scope ride on OAuthAuth.
            authentication=library_authentication(config),
        )

    def close(self) -> None:
        self._client.close()

    def _site_error(self, exc: Exception, context: str) -> DreachySiteError:
        """The DreachySiteError for *exc*; a refused login becomes DreachyAuthError."""
        if isinstance(exc, AuthenticationError):
            return DreachyAuthError(_CREDENTIALS_REFUSED)
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            # Logged in and still 401 after the library's one retry: the login is bad.
            if status == 401 and self.config.uses_oauth:
                return DreachyAuthError(_CREDENTIALS_REFUSED, status=status)
            return DreachySiteError(str(exc), status=status)
        if isinstance(exc, httpx.HTTPError):
            return DreachySiteError(str(exc))
        return DreachySiteError(f"Unexpected response for {context}: {exc!r}")

    def _get_collection(self, resource_type: str, params: DrupalJsonApiParams) -> list[dict[str, Any]]:
        try:
            response = self._client.get_collection(
                resource_type, query_string=params, raise_for_status=True, disable_cache=True
            )
            return response["data"]
        # ValueError and friends: a 200 that isn't JSON:API (a captive
        # portal, a maintenance page). Left raw, it would end the watcher's
        # task and bypass discovery's retry throttle.
        except (AuthenticationError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise self._site_error(exc, resource_type) from exc

    # -- content model --------------------------------------------------

    def get_schema(self) -> Schema:
        """Discover the site's node types and their text fields.

        Raises DreachySiteError when the JSON:API index can't be read.
        """
        bundles = self._node_bundles()
        labels = self._node_type_labels()
        samples = {bundle: self._sample_attributes(bundle) for bundle in bundles}
        return build_schema(bundles, labels, samples)

    def _index_url(self) -> str:
        locale = self.config.default_locale
        return urljoin(self._client.base_url, f"{locale}/jsonapi" if locale else "jsonapi")

    def _node_bundles(self) -> list[str]:
        try:
            response = self._client.fetch(self._index_url(), raise_for_status=True)
            links = response.json().get("links", {})
        # KeyError/TypeError: e.g. a token response without expires_in, raised
        # by the library while logging in for this request.
        except (AuthenticationError, httpx.HTTPError, ValueError, AttributeError, KeyError, TypeError) as exc:
            error = self._site_error(exc, "the JSON:API index")
            if type(error) is DreachySiteError:  # keep R1's wording for plain site failures
                error = DreachySiteError(f"JSON:API index unavailable: {exc}", status=error.status)
            raise error from exc
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
        params = _published(
            DrupalJsonApiParams().add_sort("created", "DESC").add_page_limit(self.config.schema_sample_size)
        )
        try:
            resources = self._get_collection(f"node--{bundle}", params)
        except DreachySiteError as exc:
            if isinstance(exc, DreachyAuthError):
                raise  # a bad login fails discovery; it doesn't make a type "unreadable"
            # One unreadable type (e.g. no anonymous view access) mustn't sink
            # the rest. Anything else — a 5xx, a timeout — fails the whole
            # discovery, so the retry picks the type up rather than losing it
            # for the session.
            if exc.status not in _UNREADABLE_STATUSES:
                raise
            logger.info("Can't sample node--%s, skipping it: %s", bundle, exc)
            return None
        return [resource.get("attributes") or {} for resource in resources]

    # -- editorial (R3) ---------------------------------------------------

    def can_edit(self) -> bool:
        """Logged in, and the site grants a token (checked now: one request)."""
        if not self.config.uses_oauth:
            return False
        try:
            self._client.add_authorization_header()
        except (AuthenticationError, httpx.HTTPError) as exc:
            # The type only: an exception's text is no place to risk credentials.
            logger.warning("Editing is unavailable: the site login failed (%s)", type(exc).__name__)
            return False
        return True

    def get_pending_nodes(self, limit: int | None = None) -> list[dict[str, Any]]:
        limit = limit or self.config.pending_sample_limit

        # No filter, on purpose: core JSON:API narrows any *filtered* node
        # collection to published (and the account's own) content unless it
        # has `bypass node access` (JsonapiHooks::jsonapiNodeFilterAccess), so
        # filter[status]=0 would hide everyone else's drafts. Unfiltered, each
        # item is checked on its own, where `view any unpublished content`
        # counts. So: each type's most recently changed items, unpublished
        # kept — pending *among the latest `limit` changes per type*.
        def params(type_schema: TypeSchema) -> DrupalJsonApiParams:
            return DrupalJsonApiParams().add_sort("changed", "DESC").add_page_limit(limit)

        # Same per-type rule as the other reads: one failing type is skipped.
        nodes = [
            n
            for n in self._read_each_type(self._types(), params)
            if not n["status"] and n["moderation_state"] != _ARCHIVED_STATE
        ]
        nodes.sort(key=lambda n: n["changed"], reverse=True)
        return nodes[:limit]

    def create_draft(self, content_type: str, title: str, body: str) -> dict[str, Any]:
        # Never write from the fallback table or a guess: only a discovered
        # schema says which field holds the text and whether the type is moderated.
        # _types() first: it retries a failed discovery when one is due (a site
        # that was down at start), rather than refusing notes until a read does.
        types = self._types()
        type_schema = types.get(content_type) if self.schema_discovered else None
        if type_schema is None:
            raise DreachySiteError(f"notes can't be saved as {content_type!r} on this site right now")
        attributes: dict[str, Any] = {
            type_schema.label_field: title,
            type_schema.text_fields[0]: {"value": body, "format": _NOTE_TEXT_FORMAT},
        }
        if type_schema.moderated:
            # Content Moderation forbids setting status on moderated content;
            # the state decides it. Explicit, in case the workflow's default isn't a draft.
            attributes["moderation_state"] = _DRAFT_STATE
        else:
            attributes["status"] = False
        document = {"data": {"type": f"node--{content_type}", "attributes": attributes}}
        try:
            response = self._client.create_resource(f"node--{content_type}", document)
            node = _node_to_dict(content_type, type_schema, response["data"])
        except (AuthenticationError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise self._write_error(exc, content_type) from exc
        if node["status"]:
            logger.error("drupal_create_note: the site published %s despite a draft request", node["id"])
            raise DreachySiteError(
                "the site published the note instead of keeping it as a draft — tell whoever looks after the site"
            )
        return node

    def _write_error(self, exc: Exception, content_type: str) -> DreachySiteError:
        """Why a write failed, in words the model can pass on — with the site's
        own reason when it gave one (JSON:API errors[].detail: a missing
        permission, an unknown moderation state, a required field)."""
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            reason = _error_detail(exc.response)
            if reason:
                logger.warning("drupal_create_note: the site refused the note (%s): %s", status, reason)
            suffix = f" ({reason})" if reason else ""
            if status == 405:
                return DreachySiteError(
                    "the site doesn't accept changes over JSON:API (it's in read-only mode)", status=status
                )
            if status == 403:
                return DreachySiteError(
                    f"the site doesn't let Dreachy create {content_type} drafts{suffix}", status=status
                )
            if status == 422:
                return DreachySiteError(f"the site rejected the note{suffix}", status=status)
        return self._site_error(exc, f"node--{content_type}")

    # -- queries --------------------------------------------------------

    def get_recent_nodes(self, limit: int | None = None, *, include_unpublished: bool = False) -> list[dict[str, Any]]:
        limit = limit or self.config.whats_new_limit

        def params(type_schema: TypeSchema) -> DrupalJsonApiParams:
            return _published(
                DrupalJsonApiParams().add_sort("created", "DESC").add_page_limit(limit), include_unpublished
            )

        nodes = self._read_each_type(self._types(), params)
        nodes.sort(key=lambda n: n["created"], reverse=True)
        return nodes[:limit]

    def find_content(
        self, keyword: str, content_type: str | None = None, *, include_unpublished: bool = False
    ) -> list[dict[str, Any]]:
        limit = self.config.find_content_limit
        types = self._types()
        # An unknown or disabled type (e.g. from a tool spec built before a
        # settings change) searches every enabled type rather than finding
        # nothing.
        if content_type in types:
            types = {content_type: types[content_type]}

        def params(type_schema: TypeSchema) -> DrupalJsonApiParams:
            return _published(
                DrupalJsonApiParams()
                .add_filter(type_schema.label_field, keyword, operator=FilterOperator.CONTAINS)
                .add_sort("created", "DESC")
                .add_page_limit(limit),
                include_unpublished,
            )

        matches = self._read_each_type(types, params)
        matches.sort(key=lambda n: n["created"], reverse=True)
        return matches[:limit]

    def _read_each_type(
        self, types: Schema, params_for: Callable[[TypeSchema], DrupalJsonApiParams]
    ) -> list[dict[str, Any]]:
        """Node dicts from one collection read per type.

        A type that fails on its own (deleted or locked mid-session: a 403,
        404, or an anonymous 401) is skipped with a warning, so it can't sink
        every query until a restart. A bad login, a site-wide failure, or
        every type failing at once (a wrong language prefix 404s them all)
        still raises: that's the site, not one type.
        """
        nodes: list[dict[str, Any]] = []
        skipped: list[tuple[str, DreachySiteError]] = []
        for bundle, type_schema in types.items():
            try:
                resources = self._get_collection(f"node--{bundle}", params_for(type_schema))
            except DreachySiteError as exc:
                if isinstance(exc, DreachyAuthError) or exc.status not in _UNREADABLE_STATUSES:
                    raise
                skipped.append((bundle, exc))
                continue
            nodes.extend(_node_to_dict(bundle, type_schema, resource) for resource in resources)
        if skipped and len(skipped) == len(types):
            raise skipped[0][1]
        for bundle, exc in skipped:
            logger.warning("Skipping node--%s this time: %s", bundle, exc)
        return nodes

    def get_article(self, title_or_path: str, *, include_unpublished: bool = False) -> dict[str, Any] | None:
        types = self._types()
        path = title_or_path if title_or_path.startswith("/") else f"/{title_or_path}"
        resource = None
        try:
            resource = self._client.get_resource_by_path(path, raise_for_status=True, disable_cache=True)
        except ResourceNotFoundError:
            pass
        # ValueError and friends: a router answer that isn't JSON (a captive portal).
        except (AuthenticationError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise self._site_error(exc, title_or_path) from exc

        if resource is not None:
            bundle = resource["data"]["type"].split("--")[1]
            # A site that hides `status` (e.g. JSON:API Extras): anonymous
            # access only ever sees published content, but a logged-in
            # Dreachy may see drafts, so there a missing status fails closed.
            published = resource["data"]["attributes"].get("status", not self.config.uses_oauth)
            if bundle in types and (published or include_unpublished):
                return _node_to_dict(bundle, types[bundle], resource["data"])
            # A path to something Dreachy doesn't talk about (a disabled
            # type, a taxonomy term, an unpublished node): fall through to
            # the title search.

        for bundle, type_schema in types.items():
            params = _published(
                DrupalJsonApiParams()
                .add_filter(type_schema.label_field, title_or_path, operator=FilterOperator.EQUAL)
                .add_page_limit(1),
                include_unpublished,
            )
            found = self._get_collection(f"node--{bundle}", params)
            if found:
                return _node_to_dict(bundle, type_schema, found[0])
        return None


# The pre-R2 name: existing callers and tests keep working.
DrupalClient = JsonApiBackend
