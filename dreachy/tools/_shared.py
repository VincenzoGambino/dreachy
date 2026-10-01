"""Shared Backend singleton for the Dreachy tool files.

Leading underscore keeps this out of the external-tools auto-loader (it only
scans *.py files that don't start with "_").
"""

from __future__ import annotations

import os
import threading

from dreachy.backend import Backend
from dreachy.client import JsonApiBackend
from dreachy.config import Config

_client: Backend | None = None
# The settings threadpool and the watcher can both ask right after a reset;
# without the lock each builds a client, and a discovery can land on the one
# that's thrown away.
_client_lock = threading.Lock()
# A dropped client is closed this long after a reset — not at once, since an
# in-flight tool call or the watcher (which switches within seconds) may
# still hold it. Closing releases its connections, token and credentials.
_RETIRE_AFTER_SECONDS = 60.0


def get_client() -> Backend:
    global _client
    client = _client
    if client is not None:
        return client
    with _client_lock:
        if _client is None:
            # Discovery only once a real site is configured: the placeholder
            # base_url would send it to example.com.
            _client = JsonApiBackend(Config.from_env(), auto_discover=bool(os.environ.get("DREACHY_BASE_URL")))
        return _client


def reset_client() -> None:
    """Drop the cached client, and with it the cached schema.

    Called whenever the settings page saves, so a new site URL, language
    prefix or type selection takes effect immediately — no app restart
    needed — and the next use rediscovers the site's content types.
    """
    global _client
    with _client_lock:
        old, _client = _client, None
    close = getattr(old, "close", None)
    if close is not None:
        timer = threading.Timer(_RETIRE_AFTER_SECONDS, close)
        timer.daemon = True
        timer.start()


def type_choices() -> list[str]:
    """Machine names of the types Dreachy talks about, for tool specs. No network."""
    return list(get_client().schema)


def or_list(names: list[str]) -> str:
    """``["article", "page", "recipe"]`` -> ``"article, page, or recipe"``."""
    if len(names) <= 2:
        return " or ".join(names)
    return f"{', '.join(names[:-1])}, or {names[-1]}"
