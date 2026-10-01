"""MCP call chains with handle-token passing (R4 Task 3).

Against FakeEntityStore: handles are session-scoped, and field_set_value
retires the handle it was given — the newest token is the only valid one."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from _fake_mcp import FakeEntityStore, FakeMcpSite, FakeTokenEndpoint

from dreachy.backend import DreachySiteError
from dreachy.config import Config
from dreachy.mcp_chain import Handle, field_values, load, save, set_value, stub
from dreachy.mcp_session import McpSession


@dataclass(frozen=True)
class _Tools:
    """A hand-built mapping (Task 4 discovers the real one)."""

    load: str = "demo_entity_load_by_id"
    field_values: str = "demo_entity_field_values"
    stub: str = "demo_entity_stub"
    set_value: str = "demo_field_set_value"
    save: str = "demo_entity_save"
    token_key: str = "token"

    def load_args(self, entity_type: str, entity_id: Any) -> dict:
        return {"entity_type": entity_type, "id": entity_id}

    def values_args(self, token: str) -> dict:
        return {"token": token}

    def stub_args(self, entity_type: str, bundle: str) -> dict:
        return {"entity_type": entity_type, "bundle": bundle}

    def set_args(self, token: str, field: str, value: Any) -> dict:
        return {"token": token, "field": field, "value": value}

    def save_args(self, token: str) -> dict:
        return {"token": token}


_MAP = _Tools()


def _store() -> FakeEntityStore:
    return FakeEntityStore({7: {"bundle": "article", "fields": {"title": "Herbs", "body": {"value": "<p>Grow them.</p>"}}}})


def _run(site: FakeMcpSite, chain):
    session = McpSession(
        Config(base_url="https://site.test", auth="oauth", oauth_client_id="dreachy", oauth_client_secret="s"),
        endpoint="https://site.test/mcp",
        http_client_factory=site.client_factory(),
        token_http_client=FakeTokenEndpoint().client(),
    )
    return session.run(chain)


def test_load_then_field_values_in_one_session() -> None:
    store = _store()
    site = FakeMcpSite(store.tools())

    async def chain(caller):
        handle = await load(caller, _MAP, "node", 7)
        return await field_values(caller, _MAP, handle)

    fields = _run(site, chain)

    assert fields["title"] == "Herbs"
    assert len(set(site.session_ids())) == 1


def test_the_chain_always_uses_the_newest_token() -> None:
    store = _store()
    site = FakeMcpSite(store.tools())

    async def chain(caller):
        first = await stub(caller, _MAP, "node", "article")
        second = await set_value(caller, _MAP, first, "title", "Park bench")
        third = await set_value(caller, _MAP, second, "status", False)
        saved = await save(caller, _MAP, third)
        return first, second, third, saved

    first, second, third, saved = _run(site, chain)

    assert len({first.token, second.token, third.token}) == 3
    assert store.nodes[saved["id"]]["fields"] == {"title": "Park bench", "status": False}


def test_a_stale_handle_is_refused() -> None:
    site = FakeMcpSite(_store().tools())

    async def chain(caller):
        first = await stub(caller, _MAP, "node", "article")
        await set_value(caller, _MAP, first, "title", "x")
        return await set_value(caller, _MAP, first, "title", "y")  # reuses the retired handle

    with pytest.raises(DreachySiteError) as excinfo:
        _run(site, chain)

    assert "stale" in str(excinfo.value)


def test_a_stub_is_never_saved_unless_save_is_called() -> None:
    store = _store()
    site = FakeMcpSite(store.tools())

    async def chain(caller):
        handle = await stub(caller, _MAP, "node", "article")
        return await set_value(caller, _MAP, handle, "title", "Never saved")

    _run(site, chain)

    assert store.saves == []
    assert list(store.nodes) == [7]


def test_a_handle_does_not_outlive_its_session() -> None:
    site = FakeMcpSite(_store().tools())

    async def first_session(caller):
        return await load(caller, _MAP, "node", 7)

    handle = _run(site, first_session)

    async def second_session(caller):
        return await field_values(caller, _MAP, handle)

    with pytest.raises(DreachySiteError):
        _run(site, second_session)


def test_a_response_without_a_handle_is_a_site_error() -> None:
    site = FakeMcpSite({"demo_entity_stub": lambda arguments, state: {"type": "node"}})

    async def chain(caller):
        return await stub(caller, _MAP, "node", "article")

    with pytest.raises(DreachySiteError) as excinfo:
        _run(site, chain)

    assert "demo_entity_stub" in str(excinfo.value)


def test_handles_are_opaque_values() -> None:
    assert Handle("{{entity:node:7:h1}}").token == "{{entity:node:7:h1}}"
