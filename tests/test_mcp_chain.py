"""MCP call chains with handle-token passing (R4 Task 3, corrected by Task 1).

Against FakeEntityStore, which answers with the sandbox's recorded names and
shapes: handles outlive their session and are immutable snapshots —
field_set_value returns a NEW handle, and the old one silently shows the
entity without the change. The newest token is the only correct one."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from _fake_mcp import FakeEntityStore, FakeMcpSite, FakeTokenEndpoint, Raw, handle_string

from dreachy.backend import DreachySiteError
from dreachy.config import Config
from dreachy.mcp_chain import Handle, field_values, load, save, set_value, stub
from dreachy.mcp_session import McpSession

_RECORDED = Path(__file__).parent / "fixtures" / "mcp" / "responses"


@dataclass(frozen=True)
class _Tools:
    """A hand-built mapping with the recorded argument names (Task 4 discovers the real one)."""

    load: str = "tool_api__demo_entity_load_by_id"
    field_values: str = "tool_api__demo_entity_field_values"
    stub: str = "tool_api__demo_entity_stub"
    set_value: str = "tool_api__demo_field_set_value"
    save: str = "tool_api__demo_entity_save"

    def load_args(self, entity_type: str, entity_id: int) -> dict:
        return {"entity_type_id": entity_type, "entity_id": entity_id}

    def values_args(self, token: str, field: str | None = None) -> dict:
        return {"entity": token, **({"fields": field} if field else {})}

    def stub_args(self, entity_type: str, bundle: str, base_fields: dict[str, Any]) -> dict:
        return {"entity_type_id": entity_type, "bundle": bundle, "base_fields": base_fields}

    def set_args(self, token: str, field: str, value: dict[str, Any]) -> dict:
        return {"entity": token, "field_name": field, "value": value}

    def save_args(self, token: str) -> dict:
        return {"entity": token}


_MAP = _Tools()


def _store() -> FakeEntityStore:
    return FakeEntityStore({7: {"bundle": "standard_page", "fields": {"title": "Herbs", "description": "Grow them."}}})


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
        return handle, await field_values(caller, _MAP, handle)

    handle, fields = _run(site, chain)

    assert fields["title"] == "Herbs"
    assert handle.metadata["bundle"] == "standard_page"
    assert len(set(site.session_ids())) == 1


def test_one_field_can_be_asked_for() -> None:
    site = FakeMcpSite(_store().tools())

    async def chain(caller):
        handle = await load(caller, _MAP, "node", 7)
        return await field_values(caller, _MAP, handle, "description")

    assert _run(site, chain) == {"description": "Grow them."}


def test_the_chain_always_uses_the_newest_token() -> None:
    store = _store()
    site = FakeMcpSite(store.tools())

    async def chain(caller):
        first = await stub(caller, _MAP, "node", "standard_page", {"title": "Park bench", "status": False})
        second = await set_value(caller, _MAP, first, "description", {"value": "Seats four."})
        third = await set_value(caller, _MAP, second, "preview_text", {"value": "A bench."})
        saved = await save(caller, _MAP, third)
        return first, second, third, saved

    first, second, third, saved = _run(site, chain)

    assert len({first.token, second.token, third.token}) == 3
    assert store.nodes[int(saved["id"])]["fields"] == {
        "title": "Park bench",
        "status": False,
        "description": "Seats four.",
        "preview_text": "A bench.",
    }


def test_a_stale_handle_silently_loses_changes() -> None:
    # Why the rule matters: the server doesn't refuse an old handle, it
    # answers with the entity as it was.
    store = _store()
    site = FakeMcpSite(store.tools())

    async def chain(caller):
        first = await stub(caller, _MAP, "node", "standard_page", {"title": "x"})
        await set_value(caller, _MAP, first, "description", {"value": "kept?"})
        stale = await set_value(caller, _MAP, first, "preview_text", {"value": "y"})
        return await save(caller, _MAP, stale)

    saved = _run(site, chain)

    assert "description" not in store.nodes[int(saved["id"])]["fields"]


def test_a_stub_is_never_saved_unless_save_is_called() -> None:
    store = _store()
    site = FakeMcpSite(store.tools())

    async def chain(caller):
        handle = await stub(caller, _MAP, "node", "standard_page", {"title": "Never saved"})
        return await set_value(caller, _MAP, handle, "description", {"value": "x"})

    _run(site, chain)

    assert store.saves == []
    assert list(store.nodes) == [7]


def test_a_handle_still_works_in_a_later_session() -> None:
    site = FakeMcpSite(_store().tools())

    async def first_session(caller):
        return await load(caller, _MAP, "node", 7)

    handle = _run(site, first_session)

    async def second_session(caller):
        return await field_values(caller, _MAP, handle, "title")

    assert _run(site, second_session) == {"title": "Herbs"}
    assert len(set(site.session_ids())) == 2


def test_a_response_without_a_handle_is_a_site_error() -> None:
    site = FakeMcpSite({_MAP.stub: lambda arguments, state: {"created_entity": "nothing to see"}})

    async def chain(caller):
        return await stub(caller, _MAP, "node", "standard_page", {"title": "x"})

    with pytest.raises(DreachySiteError) as excinfo:
        _run(site, chain)

    assert _MAP.stub in str(excinfo.value)


def test_a_save_that_reports_no_id_is_a_site_error() -> None:
    unsaved = handle_string("{{entity:abc123}}", {"entity_type": "node", "bundle": "standard_page", "id": "new"})
    site = FakeMcpSite({_MAP.save: lambda arguments, state: {"saved_entity": unsaved}})

    async def chain(caller):
        return await save(caller, _MAP, Handle("{{entity:abc123}}"))

    with pytest.raises(DreachySiteError, match="saved id"):
        _run(site, chain)


def test_the_recorded_load_and_save_answers_parse() -> None:
    def recorded(name):
        return lambda arguments, state: Raw(json.loads((_RECORDED / name).read_text())["result"])

    site = FakeMcpSite({_MAP.load: recorded("35-reload.json"), _MAP.save: recorded("34-save.json")})

    async def chain(caller):
        loaded = await load(caller, _MAP, "node", 142)
        return loaded, await save(caller, _MAP, loaded)

    loaded, saved = _run(site, chain)

    assert loaded.token == "{{entity:7b6e39}}"
    assert loaded.metadata == {"entity_type": "node", "bundle": "standard_page", "id": "142", "langcode": "en", "revision_id": "142"}
    assert saved["id"] == "142"


def test_handles_are_opaque_values() -> None:
    assert Handle("{{entity:47c0cb}}").token == "{{entity:47c0cb}}"
    assert Handle("{{entity:47c0cb}}") == Handle("{{entity:47c0cb}}", {"id": "5"})
