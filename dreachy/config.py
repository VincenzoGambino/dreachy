"""Single source of truth for all tunable constants of the Dreachy client.

All call sites import from here; nothing is hard-coded in client.py or the
tools built on top of it.
"""

import os
from dataclasses import dataclass


@dataclass
class Config:
    # ---------------------------------------------------------------------------
    # Site connection
    # ---------------------------------------------------------------------------
    # Placeholder only — every install points at its own Drupal site via
    # DREACHY_BASE_URL in .env at the instance path (see main.py / README.md).
    base_url: str = "https://example.com"
    # Multilingual sites serve JSON:API under a language prefix (/en/jsonapi/...);
    # single-language sites have none and 404 on a prefixed path. None means no
    # prefix. Set per install via DREACHY_LOCALE / the settings page.
    default_locale: str | None = "en"
    request_timeout: float = 10.0

    # ---------------------------------------------------------------------------
    # Content model — discovered from the site (see schema.py)
    # ---------------------------------------------------------------------------
    # Fallback only: the types queried until a discovery succeeds (Umami demo
    # profile — confirmed 2026-07-27).
    content_types: tuple[str, ...] = ("article", "page", "recipe")
    # The installer's selection from the settings page (DREACHY_TYPES).
    # Empty means every type the site has, including ones added later.
    enabled_types: tuple[str, ...] = ()
    # Discovery reads this many recent nodes per type to find its text
    # fields, so a field left empty on one node doesn't hide it.
    schema_sample_size: int = 5
    # After a failed discovery, queries retry it at most this often.
    schema_retry_seconds: float = 60.0

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

    @classmethod
    def from_env(cls) -> "Config":
        """A Config with the installer's settings applied (instance .env / settings page)."""
        config = cls()
        if base_url := os.environ.get("DREACHY_BASE_URL"):
            config.base_url = base_url
        # Deliberately not `if locale :=` — an empty DREACHY_LOCALE is a real
        # setting ("this site has no language prefix"), not an absent one.
        locale = os.environ.get("DREACHY_LOCALE")
        if locale is not None:
            config.default_locale = locale or None
        config.enabled_types = parse_types(os.environ.get("DREACHY_TYPES", ""))
        return config


def parse_types(value: str) -> tuple[str, ...]:
    """``"recipe, article,"`` -> ``("recipe", "article")``."""
    return tuple(part.strip() for part in value.split(",") if part.strip())
