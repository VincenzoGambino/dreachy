"""Shared DrupalClient singleton for the four Dreachy tool files.

Leading underscore keeps this out of the external-tools auto-loader (it only
scans *.py files that don't start with "_").
"""

from __future__ import annotations

import os

from dreachy.client import DrupalClient
from dreachy.config import Config

_client: DrupalClient | None = None


def get_client() -> DrupalClient:
    global _client
    if _client is None:
        # Discovery only once a real site is configured: the placeholder
        # base_url would send it to example.com.
        _client = DrupalClient(Config.from_env(), auto_discover=bool(os.environ.get("DREACHY_BASE_URL")))
    return _client


def reset_client() -> None:
    """Drop the cached client, and with it the cached schema.

    Called whenever the settings page saves, so a new site URL, language
    prefix or type selection takes effect immediately — no app restart
    needed — and the next use rediscovers the site's content types.
    """
    global _client
    _client = None
