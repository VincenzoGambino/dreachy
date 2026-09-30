"""The optional site login, end to end against a mocked private site: the
token endpoint, Bearer headers, drupal-api-client 0.3.1's refresh margin and
single 401 retry, and what a refused login looks like to the tools. The
secret must never reach an error message or the log."""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
from _fake_site import NEWS_LABELS, NEWS_NODES, FakeSite, formatted, node

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

    def __init__(
        self,
        *,
        accept: bool = True,
        expires_in: int | None = 300,
        token_status: int | None = None,
        nodes: dict | None = None,
        router_html: bool = False,
    ) -> None:
        self.site = FakeSite(nodes or NEWS_NODES, labels=NEWS_LABELS)
        # The router answers 200 with a non-JSON page (a captive portal, say).
        self.router_html = router_html
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
            grant = {"access_token": token, "token_type": "Bearer"}
            if self.expires_in is not None:  # RFC 6749 only RECOMMENDS expires_in
                grant["expires_in"] = self.expires_in
            return httpx.Response(200, json=grant)
        auth = request.headers.get("Authorization")
        self.seen_auth.append(auth)
        if auth is None or auth.removeprefix("Bearer ") not in self.valid_tokens:
            return httpx.Response(401, json={"errors": [{"status": "401"}]})
        if self.router_html and request.url.path.endswith("/router/translate-path"):
            return httpx.Response(200, text="<html>Sign in to the Wi-Fi</html>")
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


def test_the_watcher_keeps_polling_through_a_refused_login(monkeypatch) -> None:
    from dreachy.tools import drupal_watch_site as watch_module

    backend = PrivateSite(accept=False).backend(_oauth_config(poll_interval_seconds=0.01))
    polls: list[int] = []
    real_read = backend.get_recent_nodes

    def counting_read(*args, **kwargs):
        polls.append(1)
        return real_read(*args, **kwargs)

    monkeypatch.setattr(backend, "get_recent_nodes", counting_read)
    monkeypatch.setattr(watch_module, "get_client", lambda: backend)
    monkeypatch.setattr(watch_module, "_watch_task", None)
    tool = watch_module.DrupalWatchSite()
    deps = ToolDependencies(reachy_mini=None, movement_manager=None)

    async def run() -> bool:
        await tool(deps, action="start")
        await asyncio.sleep(0.3)
        alive = not watch_module._watch_task.done()
        await tool(deps, action="stop")
        return alive

    assert asyncio.run(run()) is True  # the refused login didn't end the watcher's task
    assert len(polls) >= 2  # and it kept trying, backing off


def test_a_path_lookup_through_the_router_maps_a_refused_login() -> None:
    with PrivateSite(accept=False).backend() as backend:
        with pytest.raises(DreachyAuthError):
            backend.get_article("/news_item/n2")


def test_an_unavailable_token_endpoint_is_not_blamed_on_the_credentials() -> None:
    # drupal-api-client raises AuthenticationError for any failed grant,
    # including a 503 from a site in maintenance mode, so Dreachy mustn't
    # claim the credentials were refused.
    with PrivateSite(token_status=503).backend() as backend:
        with pytest.raises(DreachyAuthError) as excinfo:
            backend.get_recent_nodes()

    assert "or is unavailable" in str(excinfo.value)


# ---------------------------------------------------------------------------
# R3 Task 1 cleanup: library errors that escaped the mapping, and a path read
# that failed open when a site hides `status`.
# ---------------------------------------------------------------------------


def test_a_token_response_without_expires_in_fails_discovery_quietly() -> None:
    with PrivateSite(expires_in=None).backend() as backend:
        assert backend.refresh_schema() is False  # must not raise: through _types() it would end the watcher


def test_a_non_json_router_answer_is_a_site_error() -> None:
    with PrivateSite(router_html=True).backend() as backend:
        with pytest.raises(DreachySiteError):
            backend.get_article("/news_item/n2")


def test_a_logged_in_path_read_treats_a_missing_status_as_unpublished() -> None:
    # A site that hides `status` (e.g. JSON:API Extras) mustn't let a draft
    # through to a logged-in Dreachy that can view drafts.
    draft = node("news_item", "d9", "Hidden-status draft", "2026-09-29T09:00:00+00:00", body=formatted("<p>Secret.</p>"))
    del draft["attributes"]["status"]
    nodes = {"news_item": [*NEWS_NODES["news_item"], draft]}

    with PrivateSite(nodes=nodes).backend() as backend:
        assert backend.get_article("/news_item/d9") is None


def test_editing_needs_a_login() -> None:
    private = PrivateSite()

    with private.backend(Config()) as backend:
        assert backend.can_edit() is False

    assert private.grants == 0


def test_editing_is_available_when_the_site_grants_a_token() -> None:
    private = PrivateSite()

    with private.backend() as backend:
        assert backend.can_edit() is True

    assert private.grants == 1


def test_editing_is_unavailable_when_the_login_is_refused(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        with PrivateSite(accept=False).backend() as backend:
            assert backend.can_edit() is False

    assert _SECRET not in caplog.text
