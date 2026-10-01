"""McpSession: one authenticated MCP session per call chain, from sync code.

Runs the real `mcp` SDK client against tests/_fake_mcp.FakeMcpSite; the
token comes from drupal-api-client's OAuth handling against a fake token
endpoint, as in R2."""

from __future__ import annotations

import logging

import pytest
from _fake_mcp import FakeMcpSite, FakeTokenEndpoint, FakeToolError

from dreachy.backend import DreachyAuthError, DreachySiteError
from dreachy.config import Config
from dreachy.mcp_session import McpSession, McpToolError

_SECRET = "s3cret-value-never-shown"
_ENDPOINT = "https://site.test/mcp"


def _config() -> Config:
    return Config(base_url="https://site.test", auth="oauth", oauth_client_id="dreachy", oauth_client_secret=_SECRET)


def _session(site: FakeMcpSite, tokens: FakeTokenEndpoint | None = None) -> McpSession:
    tokens = tokens or FakeTokenEndpoint()
    return McpSession(
        _config(),
        endpoint=_ENDPOINT,
        http_client_factory=site.client_factory(),
        token_http_client=tokens.client(),
    )


def _echo(arguments, state):
    return {"echo": arguments}


def test_a_chain_runs_in_one_session() -> None:
    site = FakeMcpSite({"demo_a": _echo, "demo_b": _echo, "demo_c": _echo})

    async def chain(caller):
        return [await caller.call(name, {"n": i}) for i, name in enumerate(("demo_a", "demo_b", "demo_c"))]

    results = _session(site).run(chain)

    assert results == [{"echo": {"n": 0}}, {"echo": {"n": 1}}, {"echo": {"n": 2}}]
    assert len(set(site.session_ids())) == 1


def test_each_run_is_a_new_session() -> None:
    site = FakeMcpSite({"demo_a": _echo})
    session = _session(site)

    async def chain(caller):
        return await caller.call("demo_a", {})

    session.run(chain)
    session.run(chain)

    assert len(set(site.session_ids())) == 2


def test_structured_content_is_read_too() -> None:
    site = FakeMcpSite({"demo_a": _echo}, structured=True)

    async def chain(caller):
        return await caller.call("demo_a", {"x": 1})

    assert _session(site).run(chain) == {"echo": {"x": 1}}


def test_the_bearer_token_comes_from_the_site_login() -> None:
    site = FakeMcpSite({"demo_a": _echo}, token="granted-by-the-site")
    tokens = FakeTokenEndpoint("granted-by-the-site")

    async def chain(caller):
        return await caller.call("demo_a", {})

    assert _session(site, tokens).run(chain) == {"echo": {}}
    assert set(site.seen_auth) == {"Bearer granted-by-the-site"}
    assert tokens.grants == 1


def test_a_refused_mcp_login_is_a_dreachy_auth_error() -> None:
    site = FakeMcpSite({"demo_a": _echo}, token="the-server-wants-another")
    tokens = FakeTokenEndpoint("good-token")

    async def chain(caller):
        return await caller.call("demo_a", {})

    with pytest.raises(DreachyAuthError):
        _session(site, tokens).run(chain)

    assert tokens.grants == 2  # one forced refresh, one retry, then give up


def test_a_refused_token_grant_is_a_dreachy_auth_error() -> None:
    site = FakeMcpSite({"demo_a": _echo})

    async def chain(caller):
        return await caller.call("demo_a", {})

    with pytest.raises(DreachyAuthError):
        _session(site, FakeTokenEndpoint(accept=False)).run(chain)


def test_a_tool_error_is_a_site_error_with_the_servers_message() -> None:
    def refused(arguments, state):
        raise FakeToolError("Access denied for this account")

    site = FakeMcpSite({"demo_system_status": refused})

    async def chain(caller):
        return await caller.call("demo_system_status", {})

    with pytest.raises(McpToolError) as excinfo:
        _session(site).run(chain)

    assert isinstance(excinfo.value, DreachySiteError)
    assert "Access denied for this account" in str(excinfo.value)


def test_an_unknown_tool_is_a_site_error() -> None:
    site = FakeMcpSite({"demo_a": _echo})

    async def chain(caller):
        return await caller.call("demo_missing", {})

    with pytest.raises(DreachySiteError) as excinfo:
        _session(site).run(chain)

    assert not isinstance(excinfo.value, DreachyAuthError)
    assert "demo_missing" in str(excinfo.value)


def test_a_server_that_isnt_there_is_a_site_error() -> None:
    site = FakeMcpSite({"demo_a": _echo}, down=True)

    async def chain(caller):
        return await caller.call("demo_a", {})

    with pytest.raises(DreachySiteError) as excinfo:
        _session(site).run(chain)

    assert not isinstance(excinfo.value, DreachyAuthError)


def test_mcp_failures_never_reveal_the_secret(caplog) -> None:
    site = FakeMcpSite({"demo_a": _echo}, token="the-server-wants-another")

    async def chain(caller):
        return await caller.call("demo_a", {})

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(DreachyAuthError) as excinfo:
            _session(site).run(chain)

    assert _SECRET not in str(excinfo.value)
    assert _SECRET not in repr(excinfo.value)
    assert _SECRET not in caplog.text


def test_list_tools_returns_the_servers_tools() -> None:
    site = FakeMcpSite({"demo_a": _echo, "demo_b": _echo}, annotations={"demo_a": {"readOnlyHint": True}})

    tools = _session(site).list_tools()

    assert [t.name for t in tools] == ["demo_a", "demo_b"]
    assert tools[0].annotations.read_only_hint is True
