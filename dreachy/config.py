"""Single source of truth for all tunable constants of the Dreachy client.

All call sites import from here; nothing is hard-coded in client.py or the
tools built on top of it.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


logger = logging.getLogger(__name__)

AUTH_MODES = ("none", "oauth")
BACKENDS = ("jsonapi", "mcp")


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
    # Optional: a scope to request by name. Empty = none sent, and the site
    # applies the consumer's default scope (docs/drupal-setup.md).
    oauth_scope: str = ""

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
    # drupal_pending_content (R3): unpublished items sampled per type; the
    # count is capped here, like the pulse.
    pending_sample_limit: int = 50
    # drupal_create_note (R3): the content type notes are saved as. Empty =
    # the first enabled type. An installer setting — the model never picks.
    note_type: str = ""
    # No true collection count in core JSON:API; get_site_pulse() approximates
    # counts by capping a fetch at this many items per type and taking len().
    pulse_sample_limit: int = 50

    # ---------------------------------------------------------------------------
    # Backend (R4): JSON:API (the default) or the site's MCP server
    # ---------------------------------------------------------------------------
    backend: str = "jsonapi"
    # Empty = {base_url}/mcp.
    mcp_endpoint: str = ""
    # The Search API index content searches use; empty = the search tool's
    # schema enum, if it has one (mcp_mapping.py).
    mcp_search_index: str = ""
    # Operation -> tool name overrides, plus "search_params" (DREACHY_MCP_MAPPING).
    mcp_mapping: dict = field(default_factory=dict)
    # The node types to use, instead of discovering them from a listing of
    # all content (which can't see a type with no content yet).
    mcp_bundles: tuple[str, ...] = ()
    # Allowlisted extra tools for drupal_site_action (Task 8).
    mcp_extra_tools: tuple[str, ...] = ()
    # Calls run at once in one session: the sandbox answered 503 at 6.
    mcp_concurrency: int = 3
    # Items a read lists to find the newest content of the enabled types,
    # and pending items (Ruling 4: about 20).
    mcp_window: int = 50
    mcp_pending_window: int = 20

    # ---------------------------------------------------------------------------
    # Watcher (drupal_watch_site)
    # ---------------------------------------------------------------------------
    poll_interval_seconds: float = 30.0
    # On a failed poll, the interval doubles each consecutive failure (backing
    # off a down site rather than hammering it), capped at this many seconds.
    watch_max_backoff_seconds: float = 300.0

    @property
    def effective_mcp_endpoint(self) -> str:
        return self.mcp_endpoint or f"{self.base_url.rstrip('/')}/mcp"

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
        config.note_type = os.environ.get("DREACHY_NOTE_TYPE", "").strip()
        auth = os.environ.get("DREACHY_AUTH", "none").strip().lower() or "none"
        if auth not in AUTH_MODES:
            logger.warning("Unknown DREACHY_AUTH %r; using anonymous access", auth)
            auth = "none"
        config.auth = auth
        config.oauth_client_id = os.environ.get("DREACHY_OAUTH_CLIENT_ID", "").strip()
        config.oauth_client_secret = os.environ.get("DREACHY_OAUTH_CLIENT_SECRET", "").strip()
        config.oauth_scope = os.environ.get("DREACHY_OAUTH_SCOPE", "").strip()
        if auth == "oauth" and not config.uses_oauth:
            logger.warning("OAuth is selected but the client ID or secret is missing; using anonymous access")
        _read_backend(config)
        if config.uses_oauth and config.base_url.startswith("http://"):
            logger.warning("The site login sends Dreachy's client secret over plain http; use https")
        return config


def _read_backend(config: Config) -> None:
    """The R4 backend settings (DREACHY_BACKEND and DREACHY_MCP_*)."""
    backend = os.environ.get("DREACHY_BACKEND", "").strip().lower() or "jsonapi"
    if backend not in BACKENDS:
        logger.warning("Unknown DREACHY_BACKEND %r; using JSON:API", backend)
        backend = "jsonapi"
    config.backend = backend
    endpoint = os.environ.get("DREACHY_MCP_ENDPOINT", "").strip()
    if endpoint and not same_site(endpoint, config.base_url):
        # The site's access token goes with every MCP request: never to another host.
        logger.warning("Ignoring DREACHY_MCP_ENDPOINT %s: it isn't on the site's own address", endpoint)
        endpoint = ""
    config.mcp_endpoint = endpoint
    config.mcp_search_index = os.environ.get("DREACHY_MCP_SEARCH_INDEX", "").strip()
    raw_mapping = os.environ.get("DREACHY_MCP_MAPPING", "").strip()
    if raw_mapping:
        try:
            mapping = json.loads(raw_mapping)
        except ValueError:
            mapping = None
        if isinstance(mapping, dict):
            config.mcp_mapping = mapping
        else:
            logger.warning("Ignoring DREACHY_MCP_MAPPING: it isn't a JSON object")
    config.mcp_bundles = parse_types(os.environ.get("DREACHY_MCP_BUNDLES", ""))
    config.mcp_extra_tools = parse_types(os.environ.get("DREACHY_MCP_EXTRA_TOOLS", ""))
    if backend == "mcp" and not config.uses_oauth:
        logger.warning("The MCP backend needs the site login (OAuth client credentials); set it on the settings page")


def same_site(url: str, base_url: str) -> bool:
    """Same scheme and host as the site: where the site's token may go."""
    a, b = urlsplit(url), urlsplit(base_url)
    return bool(a.netloc) and (a.scheme, a.netloc.lower()) == (b.scheme, b.netloc.lower())


def parse_types(value: str) -> tuple[str, ...]:
    """``"recipe, article,"`` -> ``("recipe", "article")``."""
    return tuple(part.strip() for part in value.split(",") if part.strip())
