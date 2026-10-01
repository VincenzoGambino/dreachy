"""Dreachy — thin Level 2 wrapper delegating to reachy_mini_conversation_app.

Reachy Mini becomes the embodiment of a Drupal site: it answers questions
about what's been published, reads content aloud, reports its own "pulse",
and reacts physically to new content — see README.md for the full pitch.

This wrapper configures the conversation app to use Dreachy's bundled
profile/tools (the same env-var surface a development checkout drives
manually) and delegates straight to its own run(). Zero forked behaviour —
configuration plus delegation, nothing more.

It also serves a small settings page (custom_app_url) so an installer can
set their Drupal site URL and add persona instructions from the dashboard,
without needing SSH access to the robot.
"""

from __future__ import annotations

import logging
import os
import threading
from argparse import Namespace
from pathlib import Path
from typing import Literal

import dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from reachy_mini import ReachyMini, ReachyMiniApp

from dreachy.config import Config, parse_types, same_site
from dreachy.mcp_backend import McpBackend
from dreachy.tools._shared import get_client, reset_client

logger = logging.getLogger(__name__)

_PACKAGE_DIR = Path(__file__).resolve().parent
_BUNDLED_PROFILE_DIR = _PACKAGE_DIR / "profile" / "dreachy"

_EDITORIAL_TOOLS = ("drupal_pending_content", "drupal_create_note")
# Decided once at start, after discovery (_start_up): the editorial tools
# are registered only when the site login works (spec R3.1). Settings saves
# re-render the profile with the same decision.
_editorial_enabled = False
# R4: drupal_site_action, only on the MCP backend with allowlisted extras
# (decided at start, like the editorial tools).
_SITE_ACTION_TOOL = "drupal_site_action"
_site_actions_enabled = False


def _instance_path() -> Path:
    """Writable per-install directory.

    An installer points Dreachy at their own Drupal site — and optionally
    adds persona instructions — via the settings page served at
    custom_app_url (see _register_settings_routes below), which writes into
    files here. This is the same instance_path mechanism
    reachy_mini_conversation_app itself uses for its own settings.
    """
    path = Path.home() / ".local" / "share" / "dreachy"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _instance_profile_dir() -> Path:
    path = _instance_path() / "profile" / "dreachy"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _extra_instructions_path() -> Path:
    return _instance_path() / "extra_instructions.txt"


def _read_extra_instructions() -> str:
    path = _extra_instructions_path()
    return path.read_text().strip() if path.exists() else ""


def _write_extra_instructions(text: str) -> None:
    _extra_instructions_path().write_text(f"{text.strip()}\n" if text.strip() else "")


def _render_profile() -> None:
    """Sync the bundled profile into the writable instance path.

    greeting.txt copies verbatim; tools.txt too, plus the editorial tools
    when _editorial_enabled (decided at start: the site login works); instructions.txt gets any
    installer-supplied extra instructions appended after the built-in
    persona — appending, not replacing, keeps the built-in guardrails (e.g.
    "only answer from site content") intact regardless of what gets typed
    into the settings page.

    Only takes effect on the next app start — like the conversation app's
    own personality editor, the profile is read once at startup, not
    hot-reloaded.
    """
    dest = _instance_profile_dir()
    (dest / "greeting.txt").write_text((_BUNDLED_PROFILE_DIR / "greeting.txt").read_text())
    tools = (_BUNDLED_PROFILE_DIR / "tools.txt").read_text()
    if _editorial_enabled:
        tools = (
            tools.rstrip("\n")
            + "\n\n# Editorial tools: registered because the site login works (spec R3).\n"
            + "\n".join(_EDITORIAL_TOOLS)
            + "\n"
        )
    if _site_actions_enabled:
        tools = (
            tools.rstrip("\n")
            + "\n\n# Site actions: the MCP backend with allowlisted extras (spec R4).\n"
            + _SITE_ACTION_TOOL
            + "\n"
        )
    (dest / "tools.txt").write_text(tools)

    base_instructions = (_BUNDLED_PROFILE_DIR / "instructions.txt").read_text()
    extra = _read_extra_instructions()
    combined = base_instructions
    if extra:
        combined = f"{base_instructions.rstrip()}\n\n## ADDITIONAL INSTRUCTIONS (set by the installer)\n{extra}\n"
    (dest / "instructions.txt").write_text(combined)


def _configure_environment() -> None:
    """Point reachy_mini_conversation_app at Dreachy's profile/tools.

    Must run before anything from reachy_mini_conversation_app is imported —
    its Config class reads these env vars at import time. Profiles are read
    from the writable instance path (not the installed package) so the
    settings page can actually persist changes to instructions.txt.
    """
    os.environ["REACHY_MINI_CUSTOM_PROFILE"] = "dreachy"
    os.environ["REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY"] = str(_instance_path() / "profile")
    os.environ["REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY"] = str(_PACKAGE_DIR / "tools")


def _load_instance_env() -> None:
    """Load the instance .env into os.environ before anything reads it.

    The conversation app loads this same file too, at the start of its own
    run(). But Dreachy discovers the site's content types (so
    drupal_find_content lists the site's own) before it hands over to that
    run(), so it needs the site settings first and loads the file itself.
    """
    env_path = _instance_path() / ".env"
    if env_path.exists():
        dotenv.load_dotenv(env_path, override=True)


def _warm_schema() -> None:
    """Discover the site's content model once at start.

    Skipped until a site URL is configured: the placeholder would send
    discovery requests to example.com. A failure leaves the Umami fallback
    in place, and the client retries discovery as it's used.
    """
    if not os.environ.get("DREACHY_BASE_URL"):
        return
    try:
        get_client().refresh_schema()
    except Exception:
        logger.exception("Content-type discovery failed at start; continuing with the fallback")


class _ConfigPayload(BaseModel):
    base_url: str = ""
    extra_instructions: str = ""
    # None = field absent from the request (leave the saved value alone);
    # "" = explicitly no language prefix, for a single-language site.
    locale: str | None = None
    # None = field absent (leave the saved selection alone); [] = every type
    # the site has, including ones added later.
    types: list[str] | None = None


class _AuthPayload(BaseModel):
    # None = field absent from the request: leave the saved value alone, so a
    # page whose login section never loaded can't wipe a working login.
    auth: Literal["none", "oauth"] | None = None
    client_id: str | None = None
    # Optional; empty = request none, and the site applies the consumer's default scope.
    scope: str | None = None
    # None or "" = keep the saved secret. It's never sent back to the page,
    # so a blank field means "unchanged", not "remove".
    client_secret: str | None = None
    clear_client_secret: bool = False


class _EditorialPayload(BaseModel):
    # None = field absent: leave the saved value alone. "" = the first enabled type.
    note_type: str | None = None


class _BackendPayload(BaseModel):
    # None = field absent: leave the saved value alone.
    backend: Literal["jsonapi", "mcp"] | None = None
    mcp_endpoint: str | None = None
    mcp_search_index: str | None = None
    mcp_bundles: list[str] | None = None
    mcp_extra_tools: list[str] | None = None


def _backend_status() -> dict:
    config = Config.from_env()
    return {
        "backend": config.backend,
        # What the tools use right now: a settings save swaps it at once.
        "active": "mcp" if isinstance(get_client(), McpBackend) else "jsonapi",
        "mcp_endpoint": config.mcp_endpoint,
        "mcp_search_index": config.mcp_search_index,
        "mcp_bundles": list(config.mcp_bundles),
        "mcp_extra_tools": list(config.mcp_extra_tools),
    }


def _editorial_status() -> dict:
    return {"available": _editorial_enabled, "note_type": os.environ.get("DREACHY_NOTE_TYPE", "")}


def _set_env(key: str, value: str) -> None:
    """Save one setting to the instance .env and the running environment.

    python-dotenv collapses two backslashes in a row when it reloads the file,
    so backslashes are escaped on the way in: every value, a secret included,
    reads back exactly as saved after a restart.
    """
    dotenv.set_key(str(_instance_path() / ".env"), key, value.replace("\\", "\\\\"))
    os.environ[key] = value


def _auth_status() -> dict:
    # From Config, so the page reports exactly what Dreachy does (Config
    # lowercases the mode and strips the values).
    config = Config.from_env()
    return {
        "auth": config.auth,
        "client_id": config.oauth_client_id,
        "scope": config.oauth_scope,
        "client_secret_set": bool(config.oauth_client_secret),
        "active": config.uses_oauth,
    }


def _register_settings_routes(settings_app: FastAPI) -> None:
    """Wire the settings page's routes onto the app's own FastAPI instance.

    GET/POST /api/config, GET /api/schema, GET /api/status, GET/POST /api/auth,
    GET/POST /api/editorial and GET/POST /api/backend.
    """

    @settings_app.get("/api/config")
    def get_config() -> dict:
        saved_locale = os.environ.get("DREACHY_LOCALE")
        return {
            "base_url": os.environ.get("DREACHY_BASE_URL", ""),
            "extra_instructions": _read_extra_instructions(),
            # Nothing saved yet: report the built-in default rather than a
            # blank, which would read as "no prefix" on the settings page.
            "locale": saved_locale if saved_locale is not None else (Config.default_locale or ""),
        }

    @settings_app.get("/api/schema")
    def get_content_types() -> dict:
        client = get_client()
        # Retry a failed discovery whenever the page asks, but never before a
        # site is configured: that would go to the placeholder URL.
        if not client.schema_discovered and os.environ.get("DREACHY_BASE_URL"):
            client.refresh_schema()
        enabled = client.schema
        return {
            "discovered": client.schema_discovered,
            "types": [
                {"id": type_id, "label": type_schema.label, "enabled": type_id in enabled}
                for type_id, type_schema in client.full_schema.items()
            ],
        }

    @settings_app.get("/api/status")
    def get_status() -> dict:
        # No discovery here: the page asks after /api/schema has tried one.
        client = get_client()
        return {"discovered": client.schema_discovered, "problem": client.last_discovery_problem}

    @settings_app.get("/api/auth")
    def get_auth() -> dict:
        return _auth_status()

    @settings_app.post("/api/auth")
    def save_auth(payload: _AuthPayload) -> dict:
        env_path = _instance_path() / ".env"
        fields = {
            "DREACHY_AUTH": payload.auth,
            "DREACHY_OAUTH_CLIENT_ID": payload.client_id,
            "DREACHY_OAUTH_SCOPE": payload.scope,
        }
        updates = {key: value.strip() for key, value in fields.items() if value is not None}
        # Blank, or only spaces, means "keep the saved secret". A newly typed
        # secret wins over "Remove the saved secret".
        new_secret = (payload.client_secret or "").strip()
        if new_secret:
            updates["DREACHY_OAUTH_CLIENT_SECRET"] = new_secret
        for key, value in updates.items():
            _set_env(key, value)
        if payload.clear_client_secret and not new_secret:
            env_path.touch()
            dotenv.unset_key(str(env_path), "DREACHY_OAUTH_CLIENT_SECRET")
            os.environ.pop("DREACHY_OAUTH_CLIENT_SECRET", None)
        # The .env may now hold a client secret (it already holds HF_TOKEN):
        # owner-only.
        env_path.chmod(0o600)
        reset_client()  # the next request logs in with the new settings
        return _auth_status()

    @settings_app.get("/api/editorial")
    def get_editorial() -> dict:
        return _editorial_status()

    @settings_app.post("/api/editorial")
    def save_editorial(payload: _EditorialPayload) -> dict:
        if payload.note_type is not None:
            note_type = payload.note_type.strip()
            _set_env("DREACHY_NOTE_TYPE", note_type)
            reset_client()  # the next note is saved as this type
        return _editorial_status()

    @settings_app.get("/api/backend")
    def get_backend() -> dict:
        return _backend_status()

    @settings_app.post("/api/backend")
    def save_backend(payload: _BackendPayload) -> dict:
        endpoint = payload.mcp_endpoint.strip() if payload.mcp_endpoint is not None else None
        if endpoint and not same_site(endpoint, os.environ.get("DREACHY_BASE_URL", "")):
            # The site's access token goes with every MCP request.
            raise HTTPException(status_code=400, detail="The MCP endpoint must be on the site's own address.")
        updates = {
            "DREACHY_BACKEND": payload.backend,
            "DREACHY_MCP_ENDPOINT": endpoint,
            "DREACHY_MCP_SEARCH_INDEX": payload.mcp_search_index.strip() if payload.mcp_search_index is not None else None,
            "DREACHY_MCP_BUNDLES": ",".join(parse_types(",".join(payload.mcp_bundles))) if payload.mcp_bundles is not None else None,
            "DREACHY_MCP_EXTRA_TOOLS": (
                ",".join(parse_types(",".join(payload.mcp_extra_tools))) if payload.mcp_extra_tools is not None else None
            ),
        }
        for key, value in updates.items():
            if value is not None:
                _set_env(key, value)
        reset_client()  # the next request uses the chosen backend
        return _backend_status()

    @settings_app.post("/api/config")
    def save_config(payload: _ConfigPayload) -> dict:
        env_path = _instance_path() / ".env"

        base_url = payload.base_url.strip()
        previous_base_url = os.environ.get("DREACHY_BASE_URL")
        base_url_changed = False
        if base_url and base_url != previous_base_url:
            _set_env("DREACHY_BASE_URL", base_url)
            base_url_changed = True

        locale_changed = False
        if payload.locale is not None:
            locale = payload.locale.strip()
            if locale != os.environ.get("DREACHY_LOCALE"):
                _set_env("DREACHY_LOCALE", locale)
                locale_changed = True

        types = None
        if payload.types is not None:
            types = ",".join(parse_types(",".join(payload.types)))
        elif base_url_changed and previous_base_url:
            # Moving to another site: the page sends no selection then, and one
            # made for the previous site would silently filter this one.
            types = ""
        login_cleared = False
        if base_url_changed and previous_base_url and os.environ.get("DREACHY_OAUTH_CLIENT_SECRET"):
            # Credentials belong to the site that issued them. Kept, the next
            # token request would POST the secret to whatever the new URL
            # points at — so moving site means entering the new site's login.
            _set_env("DREACHY_AUTH", "none")
            dotenv.unset_key(str(env_path), "DREACHY_OAUTH_CLIENT_SECRET")
            os.environ.pop("DREACHY_OAUTH_CLIENT_SECRET", None)
            login_cleared = True

        types_changed = False
        if types is not None and types != os.environ.get("DREACHY_TYPES", ""):
            _set_env("DREACHY_TYPES", types)
            types_changed = True

        # Always, even when nothing above changed: the next tool call picks up
        # the new settings with no restart, and rediscovers the site's content
        # types — saving is how a type created on the site since start shows up.
        reset_client()

        _write_extra_instructions(payload.extra_instructions)
        _render_profile()  # only takes effect on the next app start

        return {
            "status": "saved",
            "base_url_applied_immediately": base_url_changed,
            "locale_applied_immediately": locale_changed,
            "types_applied_immediately": types_changed,
            "login_cleared": login_cleared,
            "instructions_require_restart": True,
        }


def _editorial_available() -> bool:
    """Whether to register the editorial tools: the site login works right now."""
    try:
        return get_client().can_edit()
    except Exception:
        # Start-up must survive a misbehaving site; editing simply stays off.
        logger.exception("Couldn't check the site login at start; editing stays off")
        return False


def _site_actions_available() -> bool:
    """Whether to register drupal_site_action: allowlisted extras exist now.
    Also fills the backend's cache, which the tool's spec is built from."""
    try:
        return bool(get_client().site_actions())
    except Exception:
        logger.exception("Couldn't check the site actions at start; they stay off")
        return False


def _start_up(settings_app: FastAPI | None) -> None:
    """Everything Dreachy does before handing over to the conversation app."""
    global _editorial_enabled, _site_actions_enabled
    _load_instance_env()
    _configure_environment()
    # Routes before discovery: a saved URL that hangs keeps discovery waiting
    # on its timeout, and the settings page is how an installer fixes it.
    if settings_app is not None:
        _register_settings_routes(settings_app)
    _warm_schema()
    _editorial_enabled = _editorial_available()
    _site_actions_enabled = _site_actions_available()
    # Last: tools.txt depends on the login check. The conversation app reads
    # the profile only after _start_up returns.
    _render_profile()


class Dreachy(ReachyMiniApp):
    """Reachy Mini becomes the embodiment of a Drupal site."""

    # Settings page: lets an installer set their Drupal site URL and add
    # persona instructions from the dashboard, without SSH.
    custom_app_url: str | None = "http://0.0.0.0:8042"
    # Dreachy has no camera-based tools; skip the video pipeline entirely.
    request_media_backend: str | None = "gstreamer_no_video"

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        _start_up(self.settings_app)

        # Imported here, not at module level: must happen after
        # _configure_environment() has set the env vars its Config reads.
        from reachy_mini_conversation_app.main import run as run_conversation_app

        args = Namespace(no_camera=True, ui=False, debug=False, robot_name=None, command=None)
        run_conversation_app(
            args,
            robot=reachy_mini,
            app_stop_event=stop_event,
            instance_path=str(_instance_path()),
        )


if __name__ == "__main__":
    app = Dreachy()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()
