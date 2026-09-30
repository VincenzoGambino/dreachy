"""Single source of truth for all tunable constants of the Dreachy client.

All call sites import from here; nothing is hard-coded in client.py or the
tools built on top of it.
"""

import logging
import os
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)

AUTH_MODES = ("none", "oauth")


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
    # Site login (R2) — optional; anonymous is the default
    # ---------------------------------------------------------------------------
    # "none": anonymous. "oauth": OAuth2 client credentials, handled by
    # drupal-api-client — used only when both the id and the secret are set.
    auth: str = "none"
    oauth_client_id: str = ""
    # repr=False: Configs get logged and passed around; the secret mustn't
    # ride along (spec Invariants: secrets confined to the client/auth layer).
    oauth_client_secret: str = field(default="", repr=False)

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

    @property
    def uses_oauth(self) -> bool:
        return self.auth == "oauth" and bool(self.oauth_client_id and self.oauth_client_secret)

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
        auth = os.environ.get("DREACHY_AUTH", "none").strip().lower() or "none"
        if auth not in AUTH_MODES:
            logger.warning("Unknown DREACHY_AUTH %r; using anonymous access", auth)
            auth = "none"
        config.auth = auth
        config.oauth_client_id = os.environ.get("DREACHY_OAUTH_CLIENT_ID", "").strip()
        config.oauth_client_secret = os.environ.get("DREACHY_OAUTH_CLIENT_SECRET", "").strip()
        if auth == "oauth" and not config.uses_oauth:
            logger.warning("OAuth is selected but the client ID or secret is missing; using anonymous access")
        return config


def parse_types(value: str) -> tuple[str, ...]:
    """``"recipe, article,"`` -> ``("recipe", "article")``."""
    return tuple(part.strip() for part in value.split(",") if part.strip())
