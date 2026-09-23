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
        config = Config()
        if base_url := os.environ.get("DREACHY_BASE_URL"):
            config.base_url = base_url
        # Deliberately not `if locale :=` — an empty DREACHY_LOCALE is a real
        # setting ("this site has no language prefix"), not an absent one.
        locale = os.environ.get("DREACHY_LOCALE")
        if locale is not None:
            config.default_locale = locale or None
        _client = DrupalClient(config)
    return _client


def reset_client() -> None:
    """Drop the cached client so the next get_client() rebuilds it.

    Called after the settings page updates DREACHY_BASE_URL, so a new site
    URL takes effect immediately — no app restart needed.
    """
    global _client
    _client = None
