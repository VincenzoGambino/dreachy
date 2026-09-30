"""Tests for the settings page's GET/POST /api/config routes and profile rendering.

No real robot, no real HF backend: exercises the FastAPI routes directly via
TestClient, against a scratch instance path and bundled profile dir.
"""

from __future__ import annotations

import os

import pytest
from _fake_site import NEWS_LABELS, NEWS_NODES, FakeSite
from fastapi import FastAPI
from fastapi.testclient import TestClient

import dreachy.main as dreachy_main
import dreachy.tools._shared as shared
from dreachy.config import Config


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

    for key in ("DREACHY_BASE_URL", "DREACHY_LOCALE", "DREACHY_TYPES"):
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
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


# ---------------------------------------------------------------------------
# Start-up: the instance .env is loaded and the schema warmed before the
# conversation app builds its tool specs.
# ---------------------------------------------------------------------------


class _RecordingClient:
    def __init__(self) -> None:
        self.refreshes = 0

    def refresh_schema(self) -> bool:
        self.refreshes += 1
        return True


def test_load_instance_env_puts_saved_settings_into_the_environment() -> None:
    (dreachy_main._instance_path() / ".env").write_text(
        "DREACHY_BASE_URL=https://saved.example\nDREACHY_TYPES=recipe\n"
    )

    dreachy_main._load_instance_env()

    assert os.environ["DREACHY_BASE_URL"] == "https://saved.example"
    assert os.environ["DREACHY_TYPES"] == "recipe"


def test_load_instance_env_is_a_no_op_on_a_fresh_install() -> None:
    dreachy_main._load_instance_env()

    assert "DREACHY_BASE_URL" not in os.environ


def test_warm_schema_discovers_once_a_site_url_is_set(monkeypatch) -> None:
    recorder = _RecordingClient()
    monkeypatch.setattr(dreachy_main, "get_client", lambda: recorder)
    monkeypatch.setenv("DREACHY_BASE_URL", "https://site.example")

    dreachy_main._warm_schema()

    assert recorder.refreshes == 1


def test_warm_schema_sends_nothing_before_a_site_url_is_set(monkeypatch) -> None:
    recorder = _RecordingClient()
    monkeypatch.setattr(dreachy_main, "get_client", lambda: recorder)

    dreachy_main._warm_schema()

    assert recorder.refreshes == 0


def test_warm_schema_survives_an_unexpected_discovery_error(monkeypatch) -> None:
    class _Exploding:
        def refresh_schema(self) -> bool:
            raise KeyError("attributes")

    monkeypatch.setattr(dreachy_main, "get_client", lambda: _Exploding())
    monkeypatch.setenv("DREACHY_BASE_URL", "https://site.example")

    dreachy_main._warm_schema()  # must not raise: start-up survives a misbehaving site


def test_get_client_reads_the_type_selection(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_TYPES", "recipe, article,")

    assert shared.get_client().config.enabled_types == ("recipe", "article")


# ---------------------------------------------------------------------------
# Content types: discovered list with include/exclude, persisted as
# DREACHY_TYPES; saving rediscovers.
# ---------------------------------------------------------------------------


def test_get_schema_lists_discovered_types_and_which_are_enabled(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_BASE_URL", "https://example.com")
    site = FakeSite(NEWS_NODES, labels=NEWS_LABELS)
    shared._client = site.client(Config(enabled_types=("news_item",)), auto_discover=True)

    resp = _make_client().get("/api/schema")

    assert resp.json() == {
        "discovered": True,
        "types": [
            {"id": "event", "label": "Event", "enabled": False},
            {"id": "news_item", "label": "News item", "enabled": True},
        ],
    }


def test_get_schema_reports_the_fallback_when_the_site_cannot_be_read(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_BASE_URL", "https://example.com")
    shared._client = FakeSite(NEWS_NODES, index_status=503).client(auto_discover=True)

    body = _make_client().get("/api/schema").json()

    assert body["discovered"] is False
    assert [t["id"] for t in body["types"]] == ["article", "page", "recipe"]


def test_get_schema_contacts_no_site_before_one_is_configured() -> None:
    site = FakeSite(NEWS_NODES)
    shared._client = site.client()

    _make_client().get("/api/schema")

    assert site.requests == []


def test_post_config_saves_the_type_selection() -> None:
    client = _make_client()

    resp = client.post(
        "/api/config",
        json={"base_url": "https://example.com", "extra_instructions": "", "types": ["recipe", "article"]},
    )

    assert resp.json()["types_applied_immediately"] is True
    assert os.environ["DREACHY_TYPES"] == "recipe,article"
    env_text = (dreachy_main._instance_path() / ".env").read_text()
    assert "DREACHY_TYPES='recipe,article'" in env_text or "DREACHY_TYPES=recipe,article" in env_text
    assert shared.get_client().config.enabled_types == ("recipe", "article")


def test_post_config_saves_an_empty_selection_as_every_type(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_TYPES", "recipe")

    _make_client().post("/api/config", json={"base_url": "https://example.com", "extra_instructions": "", "types": []})

    assert os.environ["DREACHY_TYPES"] == ""
    assert shared.get_client().config.enabled_types == ()


def test_post_config_without_types_leaves_the_saved_selection_alone(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_TYPES", "recipe")

    resp = _make_client().post("/api/config", json={"base_url": "https://example.com", "extra_instructions": ""})

    assert resp.json()["types_applied_immediately"] is False
    assert os.environ["DREACHY_TYPES"] == "recipe"


def test_post_config_always_drops_the_cached_schema() -> None:
    shared._client = "sentinel-old-client"

    _make_client().post("/api/config", json={"base_url": "", "extra_instructions": ""})

    # Nothing changed, but saving is also how a newly created site type gets picked up.
    assert shared._client is None


def test_post_config_moving_to_another_site_clears_the_previous_sites_selection(monkeypatch) -> None:
    # The page sends no selection when the URL changes; a selection made for
    # the old site (article,recipe) would silently filter the new one.
    monkeypatch.setenv("DREACHY_BASE_URL", "https://old.example")
    monkeypatch.setenv("DREACHY_TYPES", "article,recipe")

    resp = _make_client().post(
        "/api/config", json={"base_url": "https://new.example", "extra_instructions": "", "types": None}
    )

    assert resp.json()["types_applied_immediately"] is True
    assert os.environ["DREACHY_TYPES"] == ""
