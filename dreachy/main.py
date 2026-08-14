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

import os
import threading
from argparse import Namespace
from pathlib import Path

import dotenv
from fastapi import FastAPI
from pydantic import BaseModel
from reachy_mini import ReachyMini, ReachyMiniApp

from dreachy.tools._shared import reset_client

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


class _ConfigPayload(BaseModel):
    base_url: str = ""
    extra_instructions: str = ""


def _register_settings_routes(settings_app: FastAPI) -> None:
    """Wire the settings page's GET/POST /api/config onto the app's own FastAPI instance."""

    @settings_app.get("/api/config")
    def get_config() -> dict:
        return {
            "base_url": os.environ.get("DREACHY_BASE_URL", ""),
            "extra_instructions": _read_extra_instructions(),
        }

    @settings_app.post("/api/config")
    def save_config(payload: _ConfigPayload) -> dict:
        base_url = payload.base_url.strip()
        base_url_changed = False
        if base_url and base_url != os.environ.get("DREACHY_BASE_URL"):
            env_path = _instance_path() / ".env"
            dotenv.set_key(str(env_path), "DREACHY_BASE_URL", base_url)
            os.environ["DREACHY_BASE_URL"] = base_url
            reset_client()  # next tool call picks up the new site immediately, no restart
            base_url_changed = True

        _write_extra_instructions(payload.extra_instructions)
        _render_profile()  # only takes effect on the next app start

        return {
            "status": "saved",
            "base_url_applied_immediately": base_url_changed,
            "instructions_require_restart": True,
        }


class Dreachy(ReachyMiniApp):
    """Reachy Mini becomes the embodiment of a Drupal site."""

    # Settings page: lets an installer set their Drupal site URL and add
    # persona instructions from the dashboard, without SSH.
    custom_app_url: str | None = "http://0.0.0.0:8042"
    # Dreachy has no camera-based tools; skip the video pipeline entirely.
    request_media_backend: str | None = "gstreamer_no_video"

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        _render_profile()
        _configure_environment()

        if self.settings_app is not None:
            _register_settings_routes(self.settings_app)

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
