"""Config.from_env: the installer's settings, including the optional site login."""

from __future__ import annotations

import logging

import pytest

from dreachy.config import Config

_SECRET = "s3cret-value-never-shown"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("DREACHY_BASE_URL", "DREACHY_AUTH", "DREACHY_OAUTH_CLIENT_ID", "DREACHY_OAUTH_CLIENT_SECRET", "DREACHY_OAUTH_SCOPE"):
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)


def test_anonymous_is_the_default() -> None:
    config = Config.from_env()

    assert (config.auth, config.uses_oauth) == ("none", False)


def test_oauth_with_id_and_secret_is_used(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_AUTH", "oauth")
    monkeypatch.setenv("DREACHY_OAUTH_CLIENT_ID", "dreachy")
    monkeypatch.setenv("DREACHY_OAUTH_CLIENT_SECRET", _SECRET)

    config = Config.from_env()

    assert config.uses_oauth is True
    assert (config.oauth_client_id, config.oauth_client_secret) == ("dreachy", _SECRET)


def test_oauth_without_a_secret_falls_back_to_anonymous(monkeypatch, caplog) -> None:
    monkeypatch.setenv("DREACHY_AUTH", "oauth")
    monkeypatch.setenv("DREACHY_OAUTH_CLIENT_ID", "dreachy")

    with caplog.at_level(logging.WARNING):
        config = Config.from_env()

    assert config.uses_oauth is False
    assert "anonymous" in caplog.text


def test_an_unknown_auth_mode_means_anonymous(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_AUTH", "kerberos")

    assert Config.from_env().auth == "none"


def test_config_repr_never_shows_the_client_secret(monkeypatch) -> None:
    monkeypatch.setenv("DREACHY_AUTH", "oauth")
    monkeypatch.setenv("DREACHY_OAUTH_CLIENT_ID", "dreachy")
    monkeypatch.setenv("DREACHY_OAUTH_CLIENT_SECRET", _SECRET)

    assert _SECRET not in repr(Config.from_env())


def test_an_oauth_scope_is_optional(monkeypatch) -> None:
    assert Config.from_env().oauth_scope == ""

    monkeypatch.setenv("DREACHY_OAUTH_SCOPE", " dreachy ")

    assert Config.from_env().oauth_scope == "dreachy"


def test_a_login_over_plain_http_is_warned_about(monkeypatch, caplog) -> None:
    monkeypatch.setenv("DREACHY_BASE_URL", "http://intranet.example")
    monkeypatch.setenv("DREACHY_AUTH", "oauth")
    monkeypatch.setenv("DREACHY_OAUTH_CLIENT_ID", "dreachy")
    monkeypatch.setenv("DREACHY_OAUTH_CLIENT_SECRET", _SECRET)

    with caplog.at_level(logging.WARNING):
        Config.from_env()

    assert "plain http" in caplog.text
    assert _SECRET not in caplog.text
