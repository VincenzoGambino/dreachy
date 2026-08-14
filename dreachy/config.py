"""Single source of truth for all tunable constants of the Dreachy client.

All call sites import from here; nothing is hard-coded in client.py or the
tools built on top of it.
"""

from dataclasses import dataclass


@dataclass
class Config:
    # ---------------------------------------------------------------------------
    # Site connection
    # ---------------------------------------------------------------------------
    # Placeholder only — every install points at its own Drupal site via
    # DREACHY_BASE_URL in .env at the instance path (see main.py / README.md).
    base_url: str = "https://example.com"
    default_locale: str = "en"   # Umami is multilingual; path-prefixed (/en/...)
    request_timeout: float = 10.0

    # ---------------------------------------------------------------------------
    # Content model (Umami demo profile — confirmed 2026-07-27)
    # ---------------------------------------------------------------------------
    content_types: tuple[str, ...] = ("article", "page", "recipe")

    # ---------------------------------------------------------------------------
    # Per-tool result limits
    # ---------------------------------------------------------------------------
    whats_new_limit: int = 5
    find_content_limit: int = 5
    # No true collection count in core JSON:API; get_site_pulse() approximates
    # counts by capping a fetch at this many items per type and taking len().
    pulse_sample_limit: int = 50

    # ---------------------------------------------------------------------------
    # Watcher (drupal_watch_site)
    # ---------------------------------------------------------------------------
    poll_interval_seconds: float = 30.0
    # On a failed poll, the interval doubles each consecutive failure (backing
    # off a down site rather than hammering it), capped at this many seconds.
    watch_max_backoff_seconds: float = 300.0
