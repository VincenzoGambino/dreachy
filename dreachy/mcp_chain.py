"""MCP call chains with entity-handle passing (R4).

The site's entity tools work on HANDLE TOKENS (`{{entity:...}}`): load or
stub an entity to get one, read or set fields through it, save it. What the
recorded server does (docs/mcp-findings.md "Verified from Dreachy"):

- A handle comes back inside a sentence — "Entity object handle token:
  {{entity:47c0cb}}. Entity metadata: {json}" — under `loaded_entity`,
  `created_entity`, `updated_entity` or `saved_entity`.
- Handles are immutable snapshots. field_set_value returns a NEW handle with
  the change; the old one still answers, without it. So: always use the
  newest. set_value() returns it; callers rebind
  (`handle = await set_value(...)`) and never reuse the previous one.
- Handles outlive their session, but a chain still runs inside one
  McpSession.run() (Ruling 2).

An unsaved stub changes nothing on the site: a chain that breaks before
save() writes nothing.

Tool names and argument shapes come from the mapping (mcp_mapping.py), never
from constants here — they're per-site configuration.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .backend import DreachySiteError
from .mcp_session import ToolCaller

_TOKEN = re.compile(r"\{\{entity:[^}]+\}\}")
_METADATA = "Entity metadata:"


@dataclass(frozen=True)
class Handle:
    """An entity handle token, opaque to Dreachy, with the metadata it came with."""

    token: str
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)


class ChainMapping(Protocol):
    """What a chain needs from the site's mapping (McpMapping satisfies it)."""

    load: str
    field_values: str
    stub: str
    set_value: str
    save: str

    def load_args(self, entity_type: str, entity_id: int) -> dict[str, Any]: ...
    def values_args(self, token: str, field: str | None = None) -> dict[str, Any]: ...
    def stub_args(self, entity_type: str, bundle: str, base_fields: dict[str, Any]) -> dict[str, Any]: ...
    def set_args(self, token: str, field: str, value: dict[str, Any]) -> dict[str, Any]: ...
    def save_args(self, token: str) -> dict[str, Any]: ...


def _handle(data: dict[str, Any], tool: str) -> Handle:
    for value in data.values():
        if isinstance(value, str) and (match := _TOKEN.search(value)):
            return Handle(match.group(0), _metadata(value))
    raise DreachySiteError(f"{tool} returned no entity handle")


def _metadata(text: str) -> dict[str, Any]:
    start = text.find(_METADATA)
    if start < 0:
        return {}
    try:
        found = json.JSONDecoder().raw_decode(text[start + len(_METADATA) :].lstrip())[0]
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


async def load(caller: ToolCaller, mapping: ChainMapping, entity_type: str, entity_id: int) -> Handle:
    data = await caller.call(mapping.load, mapping.load_args(entity_type, entity_id))
    return _handle(data, mapping.load)


async def field_values(
    caller: ToolCaller, mapping: ChainMapping, handle: Handle, field: str | None = None
) -> dict[str, Any]:
    """Every field's value, or just *field*'s (the server takes one name, not a list)."""
    data = await caller.call(mapping.field_values, mapping.values_args(handle.token, field))
    values = data.get("field_values")
    return dict(values) if isinstance(values, dict) else {}


async def stub(
    caller: ToolCaller, mapping: ChainMapping, entity_type: str, bundle: str, base_fields: dict[str, Any]
) -> Handle:
    data = await caller.call(mapping.stub, mapping.stub_args(entity_type, bundle, base_fields))
    return _handle(data, mapping.stub)


async def set_value(caller: ToolCaller, mapping: ChainMapping, handle: Handle, field: str, value: dict[str, Any]) -> Handle:
    """Set one field to *value* (one field item, e.g. {"value": "..."}).
    Returns the NEW handle; the one passed in no longer has every change."""
    data = await caller.call(mapping.set_value, mapping.set_args(handle.token, field, value))
    return _handle(data, mapping.set_value)


async def save(caller: ToolCaller, mapping: ChainMapping, handle: Handle) -> dict[str, Any]:
    """Save the entity behind *handle*; returns its metadata
    (entity_type, bundle, id, langcode, revision_id)."""
    data = await caller.call(mapping.save, mapping.save_args(handle.token))
    saved = _handle(data, mapping.save).metadata
    if not str(saved.get("id", "")).isdigit():
        raise DreachySiteError(f"{mapping.save} didn't report a saved id")
    return saved
