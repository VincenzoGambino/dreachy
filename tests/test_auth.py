"""The optional site login, end to end against a mocked private site: the
token endpoint, Bearer headers, drupal-api-client 0.3.1's refresh margin and
single 401 retry, and what a refused login looks like to the tools. The
secret must never reach an error message or the log."""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
from _fake_site import NEWS_LABELS, NEWS_NODES, FakeSite

import dreachy.tools._shared as shared
from dreachy.backend import DreachyAuthError, DreachySiteError
from dreachy.client import JsonApiBackend
from dreachy.config import Config
from dreachy.tools.drupal_whats_new import DrupalWhatsNew
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies

_SECRET = "s3cret-value-never-shown"


class PrivateSite:
    """FakeSite behind a login: every JSON:API or router request needs a
    Bearer token from POST /oauth/token (client credentials)."""

    def __init__(self, *, accept: bool = True, expires_in: int = 300, token_status: int | None = None) -> None:
        self.site = FakeSite(NEWS_NODES, labels=NEWS_LABELS)
        self.accept = accept
        self.expires_in = expires_in
        # Force this status from the token endpoint (e.g. 503: maintenance mode).
        self.token_status = token_status
        self.grants = 0
        self.valid_tokens: set[str] = set()
        self.seen_auth: list[str | None] = []
        self.token_bodies: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            self.token_bodies.append(request.content.decode())
            if self.token_status is not None:
                return httpx.Response(self.token_status, text="<html>Site under maintenance</html>")
            if not self.accept or _SECRET not in request.content.decode():
                return httpx.Response(401, json={"error": "invalid_client"})
            self.grants += 1
            token = f"token-{self.grants}"
            self.valid_tokens.add(token)
            return httpx.Response(
                200, json={"access_token": token, "expires_in": self.expires_in, "token_type": "Bearer"}
            )
        auth = request.headers.get("Authorization")
        self.seen_auth.append(auth)
        if auth is None or auth.removeprefix("Bearer ") not in self.valid_tokens:
            return httpx.Response(401, json={"errors": [{"status": "401"}]})
        return self.site.handle(request)

    def backend(self, config: Config | None = None) -> JsonApiBackend:
        config = config or _oauth_config()
        return JsonApiBackend(config, http_client=httpx.Client(transport=httpx.MockTransport(self.handle)))


def _oauth_config(**overrides) -> Config:
    # news_item: the private site's own type, so queries work without a
    # discovery first — one request per query keeps token counts exact.
    config = Config(
        auth="oauth", oauth_client_id="dreachy", oauth_client_secret=_SECRET, content_types=("news_item",)
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def test_anonymous_mode_sends_no_credentials_and_asks_for_no_token() -> None:
    private = PrivateSite()

    with private.backend(Config()) as backend:
        with pytest.raises(DreachySiteError):
            backend.get_recent_nodes()

    assert private.grants == 0
    assert private.seen_auth and set(private.seen_auth) == {None}


def test_a_logged_in_backend_reads_a_private_site() -> None:
    private = PrivateSite()

    with private.backend() as backend:
        assert backend.refresh_schema() is True
        titles = [n["title"] for n in backend.get_recent_nodes(limit=10)]

    assert "Council approves new park" in titles
    assert all(a and a.startswith("Bearer ") for a in private.seen_auth)


def test_a_token_is_reused_until_it_nears_expiry() -> None:
    private = PrivateSite(expires_in=300)

    with private.backend() as backend:
        backend.get_recent_nodes()
        backend.get_recent_nodes()

    assert private.grants == 1


def test_a_token_inside_the_refresh_margin_is_renewed_first() -> None:
    # 0.3.1 contract: refresh at < TOKEN_REFRESH_MARGIN_SECONDS (60) remaining.
    private = PrivateSite(expires_in=30)

    with private.backend() as backend:
        backend.get_recent_nodes()
        backend.get_recent_nodes()

    assert private.grants == 2


def test_a_revoked_token_is_renewed_once_and_the_request_retried() -> None:
    # 0.3.1 contract: one fresh token and one retry on 401.
    private = PrivateSite()

    with private.backend() as backend:
        backend.get_recent_nodes()
        private.valid_tokens.clear()  # the site revoked the token
        backend.get_recent_nodes()

    assert private.grants == 2


def test_a_refused_login_is_a_dreachy_auth_error() -> None:
    with PrivateSite(accept=False).backend() as backend:
        with pytest.raises(DreachyAuthError) as excinfo:
            backend.get_recent_nodes()

    assert "credentials" in str(excinfo.value)


def test_a_refused_login_never_reveals_the_secret(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        with PrivateSite(accept=False).backend() as backend:
            with pytest.raises(DreachyAuthError) as excinfo:
                backend.get_recent_nodes()

    assert _SECRET not in str(excinfo.value)
    assert _SECRET not in repr(excinfo.value)
    assert _SECRET not in caplog.text


def test_a_refused_login_fails_discovery_instead_of_skipping_types() -> None:
    with PrivateSite(accept=False).backend() as backend:
        assert backend.refresh_schema() is False
        assert backend.schema_discovered is False


def test_a_refused_login_reaches_the_tools_as_a_site_error() -> None:
    shared._client = PrivateSite(accept=False).backend()
    try:
        result = asyncio.run(DrupalWhatsNew()(ToolDependencies(reachy_mini=None, movement_manager=None)))
    finally:
        shared._client = None

    assert result["error"].startswith("I can't reach the site right now: ")
    assert "credentials" in result["error"]
    assert _SECRET not in result["error"]


def test_no_scope_is_sent_unless_one_is_configured() -> None:
    # The consumer's default scope is the documented primary path
    # (docs/drupal-setup.md); Dreachy names a scope only when told to.
    private = PrivateSite()

    with private.backend() as backend:
        backend.get_recent_nodes()

    assert private.token_bodies and all("scope=" not in body for body in private.token_bodies)


def test_a_configured_scope_is_sent_with_the_token_request() -> None:
    private = PrivateSite()

    with private.backend(_oauth_config(oauth_scope="dreachy")) as backend:
        backend.get_recent_nodes()

    assert all("scope=dreachy" in body for body in private.token_bodies)


def test_the_watcher_survives_a_refused_login() -> None:
    # DreachyAuthError is a DreachySiteError, which the watcher's loop
    # already catches and backs off on.
    assert issubclass(DreachyAuthError, DreachySiteError)


def test_an_unavailable_token_endpoint_is_not_blamed_on_the_credentials() -> None:
    # drupal-api-client raises AuthenticationError for any failed grant,
    # including a 503 from a site in maintenance mode, so Dreachy mustn't
    # claim the credentials were refused.
    with PrivateSite(token_status=503).backend() as backend:
        with pytest.raises(DreachyAuthError) as excinfo:
            backend.get_recent_nodes()

    assert "or is unavailable" in str(excinfo.value)
