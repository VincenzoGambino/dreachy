"""McpSession: one authenticated MCP session per call chain, from sync code.

Runs the real `mcp` SDK client against tests/_fake_mcp.FakeMcpSite; the
token comes from drupal-api-client's OAuth handling against a fake token
endpoint, as in R2. Result and error shapes are the sandbox's, recorded in
R4 Task 1 (tests/fixtures/mcp/)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from _fake_mcp import FakeMcpSite, FakeTokenEndpoint, FakeToolError, Raw, Reply

from dreachy.backend import DreachyAuthError, DreachySiteError
from dreachy.config import Config
from dreachy.mcp_session import McpSession, McpToolError

_SECRET = "s3cret-value-never-shown"
_ENDPOINT = "https://site.test/mcp"
_RECORDED = Path(__file__).parent / "fixtures" / "mcp" / "responses"


def _recorded(name: str) -> Raw:
    return Raw(json.loads((_RECORDED / name).read_text())["result"])


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


def test_the_result_is_the_envelopes_data() -> None:
    site = FakeMcpSite({"demo_a": lambda arguments, state: _recorded("35-reload.json")})

    async def chain(caller):
        return await caller.call("demo_a", {})

    data = _session(site).run(chain)

    assert set(data) == {"loaded_entity"}
    assert "{{entity:7b6e39}}" in data["loaded_entity"]


def test_a_text_only_envelope_is_read_too() -> None:
    body = {"success": True, "message": "ok", "data": {"x": 1}}
    site = FakeMcpSite({"demo_a": lambda arguments, state: Raw({"content": [{"type": "text", "text": json.dumps(body)}], "isError": False})})

    async def chain(caller):
        return await caller.call("demo_a", {})

    assert _session(site).run(chain) == {"x": 1}


def test_the_servers_message_comes_with_the_data() -> None:
    site = FakeMcpSite({"demo_list": lambda arguments, state: Reply({"results": []}, "Returned 0 entities out of a total 141.")})

    async def chain(caller):
        return await caller.call_with_message("demo_list", {})

    assert _session(site).run(chain) == ({"results": []}, "Returned 0 entities out of a total 141.")


def test_the_bearer_token_comes_from_the_site_login() -> None:
    site = FakeMcpSite({"demo_a": _echo}, token="granted-by-the-site")
    tokens = FakeTokenEndpoint("granted-by-the-site")

    async def chain(caller):
        return await caller.call("demo_a", {})

    assert _session(site, tokens).run(chain) == {"echo": {}}
    assert set(site.seen_auth) == {"Bearer granted-by-the-site"}
    assert tokens.grants == 1


def test_a_refused_mcp_login_is_a_dreachy_auth_error() -> None:
    # A token the site doesn't accept: Simple OAuth answers 401 with an HTML
    # body, which the SDK reports only as a generic -32603.
    site = FakeMcpSite({"demo_a": _echo}, token="the-server-wants-another")
    tokens = FakeTokenEndpoint("good-token")

    async def chain(caller):
        return await caller.call("demo_a", {})

    with pytest.raises(DreachyAuthError):
        _session(site, tokens).run(chain)

    assert tokens.grants == 2  # one forced refresh, one retry, then give up


def test_no_token_at_all_is_a_dreachy_auth_error() -> None:
    # mcp_server's own refusal: 401 with JSON-RPC -32001.
    site = FakeMcpSite({"demo_a": _echo})
    session = McpSession(
        Config(base_url="https://site.test"),
        endpoint=_ENDPOINT,
        http_client_factory=site.client_factory(),
        token_http_client=FakeTokenEndpoint().client(),
    )

    async def chain(caller):
        return await caller.call("demo_a", {})

    with pytest.raises(DreachyAuthError):
        session.run(chain)

    assert set(site.seen_auth) == {None}


def test_a_refused_token_grant_is_a_dreachy_auth_error() -> None:
    site = FakeMcpSite({"demo_a": _echo})

    async def chain(caller):
        return await caller.call("demo_a", {})

    with pytest.raises(DreachyAuthError):
        _session(site, FakeTokenEndpoint(accept=False)).run(chain)


def test_a_failed_tool_is_a_site_error_with_the_servers_message() -> None:
    # The server never sets isError: a failure is success: false.
    def refused(arguments, state):
        raise FakeToolError("Tool plugin access denied.")

    site = FakeMcpSite({"demo_system_status": refused})

    async def chain(caller):
        return await caller.call("demo_system_status", {})

    with pytest.raises(McpToolError) as excinfo:
        _session(site).run(chain)

    assert isinstance(excinfo.value, DreachySiteError)
    assert "demo_system_status: Tool plugin access denied." in str(excinfo.value)


def test_a_recorded_failure_is_a_tool_error() -> None:
    site = FakeMcpSite({"demo_load": lambda arguments, state: _recorded("11-error-bad-argument.json")})

    async def chain(caller):
        return await caller.call("demo_load", {})

    with pytest.raises(McpToolError, match="Tool plugin access denied"):
        _session(site).run(chain)


def test_html_in_a_failure_message_is_cleaned_up() -> None:
    site = FakeMcpSite({"demo_set": lambda arguments, state: _recorded("47-set-moderation-state.json")})

    async def chain(caller):
        return await caller.call("demo_set", {})

    with pytest.raises(McpToolError) as excinfo:
        _session(site).run(chain)

    assert "Invalid state transition from Draft to Draft" in str(excinfo.value)


def test_an_is_error_result_is_a_tool_error_too() -> None:
    site = FakeMcpSite({"demo_a": lambda arguments, state: Raw({"content": [{"type": "text", "text": "boom"}], "isError": True})})

    async def chain(caller):
        return await caller.call("demo_a", {})

    with pytest.raises(McpToolError, match="demo_a: boom"):
        _session(site).run(chain)


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
