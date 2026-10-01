"""MCP call chains with entity-handle passing (R4).

The site's entity tools work on HANDLE TOKENS (`{{entity:...}}`): load or
stub an entity to get one, read or set fields through it, save it. Two rules
from docs/mcp-findings.md shape every chain:

- A handle belongs to the session that issued it, so a chain runs inside one
  McpSession.run() — never across two.
- field_set_value returns a NEW handle and the old one is retired: always use
  the newest. set_value() returns it; callers rebind
  (`handle = await set_value(...)`) and never reuse the previous one.

An unsaved stub changes nothing on the site: a chain that breaks before
save() writes nothing.

Tool names and argument shapes come from the mapping (mcp_mapping.py), never
from constants here — they're per-site configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .backend import DreachySiteError
from .mcp_session import ToolCaller


@dataclass(frozen=True)
class Handle:
    """An entity handle token, opaque to Dreachy."""

    token: str


class ChainMapping(Protocol):
    """What a chain needs from the site's mapping (McpMapping satisfies it)."""

    load: str
    field_values: str
    stub: str
    set_value: str
    save: str
    token_key: str

    def load_args(self, entity_type: str, entity_id: Any) -> dict[str, Any]: ...
    def values_args(self, token: str) -> dict[str, Any]: ...
    def stub_args(self, entity_type: str, bundle: str) -> dict[str, Any]: ...
    def set_args(self, token: str, field: str, value: Any) -> dict[str, Any]: ...
    def save_args(self, token: str) -> dict[str, Any]: ...


def _handle(response: dict[str, Any], mapping: ChainMapping, tool: str) -> Handle:
    token = response.get(mapping.token_key)
    if not isinstance(token, str) or not token:
        raise DreachySiteError(f"{tool} returned no entity handle")
    return Handle(token)


async def load(caller: ToolCaller, mapping: ChainMapping, entity_type: str, entity_id: Any) -> Handle:
    response = await caller.call(mapping.load, mapping.load_args(entity_type, entity_id))
    return _handle(response, mapping, mapping.load)


async def field_values(caller: ToolCaller, mapping: ChainMapping, handle: Handle) -> dict[str, Any]:
    response = await caller.call(mapping.field_values, mapping.values_args(handle.token))
    fields = response.get("fields", response)
    return dict(fields) if isinstance(fields, dict) else {}


async def stub(caller: ToolCaller, mapping: ChainMapping, entity_type: str, bundle: str) -> Handle:
    response = await caller.call(mapping.stub, mapping.stub_args(entity_type, bundle))
    return _handle(response, mapping, mapping.stub)


async def set_value(caller: ToolCaller, mapping: ChainMapping, handle: Handle, field: str, value: Any) -> Handle:
    """Set one field. Returns the NEW handle; the one passed in is now retired."""
    response = await caller.call(mapping.set_value, mapping.set_args(handle.token, field, value))
    return _handle(response, mapping, mapping.set_value)


async def save(caller: ToolCaller, mapping: ChainMapping, handle: Handle) -> dict[str, Any]:
    """Save the entity behind *handle*; returns {type, bundle, id, revision}."""
    response = await caller.call(mapping.save, mapping.save_args(handle.token))
    if "id" not in response:
        raise DreachySiteError(f"{mapping.save} didn't report a saved id")
    return response
