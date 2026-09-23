"""Tests for the settings page's GET/POST /api/config routes and profile rendering.

No real robot, no real HF backend: exercises the FastAPI routes directly via
TestClient, against a scratch instance path and bundled profile dir.
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import dreachy.main as dreachy_main
import dreachy.tools._shared as shared


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path, monkeypatch):
    """Point Dreachy's instance path and bundled profile at a scratch dir."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    monkeypatch.setattr(dreachy_main, "_instance_path", lambda: instance_dir)

    bundled_dir = tmp_path / "bundled_profile"
    bundled_dir.mkdir()
    (bundled_dir / "instructions.txt").write_text("BASE INSTRUCTIONS\n")
    (bundled_dir / "tools.txt").write_text("drupal_whats_new\n")
    (bundled_dir / "greeting.txt").write_text("Hello!\n")
    monkeypatch.setattr(dreachy_main, "_BUNDLED_PROFILE_DIR", bundled_dir)

    monkeypatch.delenv("DREACHY_BASE_URL", raising=False)
    monkeypatch.delenv("DREACHY_LOCALE", raising=False)
    shared._client = None
    yield
    shared._client = None


def _make_client() -> TestClient:
    app = FastAPI()
    dreachy_main._register_settings_routes(app)
    return TestClient(app)


def test_get_config_returns_empty_defaults_when_nothing_saved() -> None:
    client = _make_client()

    resp = client.get("/api/config")

    assert resp.status_code == 200
    assert resp.json() == {"base_url": "", "extra_instructions": "", "locale": "en"}


def test_post_config_saves_base_url_and_applies_immediately(monkeypatch) -> None:
    client = _make_client()
    shared._client = "sentinel-old-client"

    resp = client.post("/api/config", json={"base_url": "https://example.com", "extra_instructions": ""})

    assert resp.status_code == 200
    body = resp.json()
    assert body["base_url_applied_immediately"] is True
    assert os.environ["DREACHY_BASE_URL"] == "https://example.com"
    # reset_client() was called: the cached singleton is gone, so the next
    # get_client() call would rebuild against the new URL.
    assert shared._client is None


def test_post_config_writes_base_url_into_instance_env(tmp_path) -> None:
    client = _make_client()

    client.post("/api/config", json={"base_url": "https://example.com", "extra_instructions": ""})

    env_path = dreachy_main._instance_path() / ".env"
    assert env_path.exists()
    assert "DREACHY_BASE_URL='https://example.com'" in env_path.read_text() or (
        "DREACHY_BASE_URL=https://example.com" in env_path.read_text()
    )


def test_post_config_blank_base_url_does_not_overwrite_existing() -> None:
    client = _make_client()
    os.environ["DREACHY_BASE_URL"] = "https://already-set.example"

    resp = client.post("/api/config", json={"base_url": "", "extra_instructions": ""})

    assert resp.json()["base_url_applied_immediately"] is False
    assert os.environ["DREACHY_BASE_URL"] == "https://already-set.example"


def test_get_config_round_trips_a_previously_saved_value() -> None:
    client = _make_client()
    client.post("/api/config", json={"base_url": "https://example.com", "extra_instructions": "Be brief."})

    resp = client.get("/api/config")

    assert resp.json() == {"base_url": "https://example.com", "extra_instructions": "Be brief.", "locale": "en"}


def test_post_config_appends_extra_instructions_to_the_base_persona() -> None:
    client = _make_client()

    client.post("/api/config", json={"base_url": "https://example.com", "extra_instructions": "Respond in Italian."})

    rendered = (dreachy_main._instance_profile_dir() / "instructions.txt").read_text()
    assert rendered.startswith("BASE INSTRUCTIONS")
    assert "Respond in Italian." in rendered
    # Appended, not replaced — the base persona must still be fully present.
    assert "BASE INSTRUCTIONS" in rendered


def test_render_profile_copies_tools_and_greeting_verbatim() -> None:
    dreachy_main._render_profile()

    dest = dreachy_main._instance_profile_dir()
    assert (dest / "tools.txt").read_text() == "drupal_whats_new\n"
    assert (dest / "greeting.txt").read_text() == "Hello!\n"


def test_render_profile_with_no_extra_instructions_leaves_base_unchanged() -> None:
    dreachy_main._render_profile()

    rendered = (dreachy_main._instance_profile_dir() / "instructions.txt").read_text()
    assert rendered == "BASE INSTRUCTIONS\n"


# ---------------------------------------------------------------------------
# Language prefix setting: blank means "no prefix" (single-language site), so
# it must be distinguishable from "never configured".
# ---------------------------------------------------------------------------


def test_get_config_reports_the_built_in_language_prefix_before_anything_is_saved() -> None:
    client = _make_client()

    assert client.get("/api/config").json()["locale"] == "en"


def test_post_config_saves_a_blank_locale_as_no_prefix() -> None:
    client = _make_client()

    resp = client.post("/api/config", json={"base_url": "https://example.com", "extra_instructions": "", "locale": ""})

    assert resp.status_code == 200
    assert os.environ["DREACHY_LOCALE"] == ""
    assert client.get("/api/config").json()["locale"] == ""
    # Same hot-reload path as the site URL: the cached client is dropped.
    assert shared._client is None


def test_post_config_saves_a_non_default_locale() -> None:
    client = _make_client()

    client.post("/api/config", json={"base_url": "https://example.com", "extra_instructions": "", "locale": "it"})

    assert os.environ["DREACHY_LOCALE"] == "it"
    env_text = (dreachy_main._instance_path() / ".env").read_text()
    assert "DREACHY_LOCALE='it'" in env_text or "DREACHY_LOCALE=it" in env_text


def test_get_client_builds_a_prefix_free_client_when_locale_is_blank() -> None:
    client = _make_client()
    client.post("/api/config", json={"base_url": "https://example.com", "extra_instructions": "", "locale": ""})

    assert shared.get_client().config.default_locale is None
