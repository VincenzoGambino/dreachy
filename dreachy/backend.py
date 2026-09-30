"""Backend — the interface Dreachy's tools, watcher and settings page query a
site through.

tool_queries.py, the tools, the watcher and the settings page call only
what's defined here. JsonApiBackend (client.py) is today's implementation;
R4 adds one over the Drupal MCP module. The content-model cache (the
discovered schema, the installer's type selection, retrying a failed
discovery) is the same for every backend, so it lives here: a backend
supplies get_schema() and the queries.

Node dicts, as every query returns them: ``id``, ``title``, ``type``,
``created``, ``changed`` (ISO 8601), ``path`` (alias or None), ``body``
(plain, speakable text) and ``summary``.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from types import TracebackType
from typing import Any

from .config import Config
from .schema import Schema, fallback_schema, select_types

logger = logging.getLogger(__name__)


class DreachySiteError(Exception):
    """The Drupal site is unreachable or returned an error response."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        # The HTTP status, when the site answered with an error one.
        self.status = status


class DreachyAuthError(DreachySiteError):
    """The site refused Dreachy's login (OAuth client credentials).

    A DreachySiteError, so the tools and the watcher already handle it: the
    tools report it, and the watcher backs off. The message never includes
    credentials.
    """


class Backend(ABC):
    """A site Dreachy talks about. Sync: tools call it via asyncio.to_thread()."""

    def __init__(self, config: Config, *, auto_discover: bool = False) -> None:
        self.config = config
        # auto_discover: queries retry a failed discovery (see _types). Off
        # by default, so a backend built for a test, or before a site URL is
        # configured, never sends discovery requests of its own accord.
        self._auto_discover = auto_discover
        self._full_schema: Schema = fallback_schema(config.content_types)
        self.schema_discovered = False
        self._next_discovery_at = 0.0
        self._discovery_lock = threading.Lock()

    def __enter__(self) -> Backend:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    @abstractmethod
    def close(self) -> None:
        """Release connections."""

    # -- content model ----------------------------------------------------

    @property
    def full_schema(self) -> Schema:
        """Every type Dreachy knows of, before the installer's selection."""
        return dict(self._full_schema)

    @property
    def schema(self) -> Schema:
        """The types Dreachy talks about. Never touches the network."""
        return select_types(self._full_schema, self.config.enabled_types)

    @abstractmethod
    def get_schema(self) -> Schema:
        """Discover the site's content types and their text fields.

        Raises DreachySiteError when the site can't be read.
        """

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

    # -- queries ----------------------------------------------------------

    @abstractmethod
    def get_recent_nodes(self, limit: int | None = None, *, include_unpublished: bool = False) -> list[dict[str, Any]]:
        """Newest content across the enabled types, newest first.

        The watcher polls this with limit=1. Published content only unless
        include_unpublished — which no tool sets (R3's editorial tools will).
        """

    @abstractmethod
    def find_content(
        self, keyword: str, content_type: str | None = None, *, include_unpublished: bool = False
    ) -> list[dict[str, Any]]:
        """Title keyword search across the enabled types, newest first.

        An unknown or disabled content_type searches every enabled type.
        Published content only unless include_unpublished.
        """

    @abstractmethod
    def get_article(self, title_or_path: str, *, include_unpublished: bool = False) -> dict[str, Any] | None:
        """One node, by URL path alias or exact title; None if nothing matches.

        Published content only unless include_unpublished.
        """

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
