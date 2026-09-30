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
from fastapi import FastAPI
from pydantic import BaseModel
from reachy_mini import ReachyMini, ReachyMiniApp

from dreachy.config import AUTH_MODES, Config, parse_types
from dreachy.tools._shared import get_client, reset_client

logger = logging.getLogger(__name__)

_PACKAGE_DIR = Path(__file__).resolve().parent
_BUNDLED_PROFILE_DIR = _PACKAGE_DIR / "profile" / "dreachy"


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

    tools.txt/greeting.txt copy verbatim; instructions.txt gets any
    installer-supplied extra instructions appended after the built-in
    persona — appending, not replacing, keeps the built-in guardrails (e.g.
    "only answer from site content") intact regardless of what gets typed
    into the settings page.

    Only takes effect on the next app start — like the conversation app's
    own personality editor, the profile is read once at startup, not
    hot-reloaded.
    """
    dest = _instance_profile_dir()
    for name in ("tools.txt", "greeting.txt"):
        (dest / name).write_text((_BUNDLED_PROFILE_DIR / name).read_text())

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

    The conversation app loads this same file too, but only once its audio
    stream launches — after it has built its tool specs. Dreachy needs the
    site settings earlier, to discover the site's content types before
    those specs are built (so drupal_find_content lists the site's own).
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
    auth: Literal["none", "oauth"] = "none"
    client_id: str = ""
    # None or "" = keep the saved secret. It's never sent back to the page,
    # so a blank field means "unchanged", not "remove".
    client_secret: str | None = None
    clear_client_secret: bool = False


def _auth_status() -> dict:
    auth = os.environ.get("DREACHY_AUTH", "none") or "none"
    client_id = os.environ.get("DREACHY_OAUTH_CLIENT_ID", "")
    secret_set = bool(os.environ.get("DREACHY_OAUTH_CLIENT_SECRET"))
    return {
        "auth": auth if auth in AUTH_MODES else "none",
        "client_id": client_id,
        "client_secret_set": secret_set,
        "active": auth == "oauth" and bool(client_id) and secret_set,
    }


def _register_settings_routes(settings_app: FastAPI) -> None:
    """Wire the settings page's routes onto the app's own FastAPI instance.

    GET/POST /api/config, GET /api/schema and GET/POST /api/auth.
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

    @settings_app.get("/api/auth")
    def get_auth() -> dict:
        return _auth_status()

    @settings_app.post("/api/auth")
    def save_auth(payload: _AuthPayload) -> dict:
        env_path = _instance_path() / ".env"
        updates = {"DREACHY_AUTH": payload.auth, "DREACHY_OAUTH_CLIENT_ID": payload.client_id.strip()}
        if payload.client_secret and not payload.clear_client_secret:
            updates["DREACHY_OAUTH_CLIENT_SECRET"] = payload.client_secret.strip()
        for key, value in updates.items():
            dotenv.set_key(str(env_path), key, value)
            os.environ[key] = value
        if payload.clear_client_secret:
            env_path.touch()
            dotenv.unset_key(str(env_path), "DREACHY_OAUTH_CLIENT_SECRET")
            os.environ.pop("DREACHY_OAUTH_CLIENT_SECRET", None)
        # The .env may now hold a client secret (it already holds HF_TOKEN):
        # owner-only.
        env_path.chmod(0o600)
        reset_client()  # the next request logs in with the new settings
        return _auth_status()

    @settings_app.post("/api/config")
    def save_config(payload: _ConfigPayload) -> dict:
        env_path = _instance_path() / ".env"

        base_url = payload.base_url.strip()
        previous_base_url = os.environ.get("DREACHY_BASE_URL")
        base_url_changed = False
        if base_url and base_url != previous_base_url:
            dotenv.set_key(str(env_path), "DREACHY_BASE_URL", base_url)
            os.environ["DREACHY_BASE_URL"] = base_url
            base_url_changed = True

        locale_changed = False
        if payload.locale is not None:
            locale = payload.locale.strip()
            if locale != os.environ.get("DREACHY_LOCALE"):
                dotenv.set_key(str(env_path), "DREACHY_LOCALE", locale)
                os.environ["DREACHY_LOCALE"] = locale
                locale_changed = True

        types = None
        if payload.types is not None:
            types = ",".join(parse_types(",".join(payload.types)))
        elif base_url_changed and previous_base_url:
            # Moving to another site: the page sends no selection then, and one
            # made for the previous site would silently filter this one.
            types = ""
        types_changed = False
        if types is not None and types != os.environ.get("DREACHY_TYPES", ""):
            dotenv.set_key(str(env_path), "DREACHY_TYPES", types)
            os.environ["DREACHY_TYPES"] = types
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
            "instructions_require_restart": True,
        }


def _start_up(settings_app: FastAPI | None) -> None:
    """Everything Dreachy does before handing over to the conversation app."""
    _load_instance_env()
    _render_profile()
    _configure_environment()
    # Routes before discovery: a saved URL that hangs keeps discovery waiting
    # on its timeout, and the settings page is how an installer fixes it.
    if settings_app is not None:
        _register_settings_routes(settings_app)
    _warm_schema()

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
