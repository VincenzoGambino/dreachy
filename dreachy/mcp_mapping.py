"""Which of a site's MCP tools does what, and how to call them (R4).

Tool names are per-site configuration, never constants: the sandbox serves
`tool_api__demo_entity_list`, another site may serve `mysite_entity_list`.
discover_mapping() matches each operation by SUFFIX in the server's
tools/list; DREACHY_MCP_MAPPING overrides win over discovery. Argument names
are the ones the recorded server takes (docs/mcp-findings.md "Verified from
Dreachy").

Two rules hold whatever the configuration says:
- search always sends check_access TRUE (findings, consequence 3);
- a tool that publishes, sets the homepage or a site default, deletes or
  discards — by name, or by its destructiveHint — is never used, as a
  mapping or as an extra (Ruling 1, extended after Task 1).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from mcp import types as mcp_types

from .backend import DreachySiteError

logger = logging.getLogger(__name__)

# operation -> the suffix of the site's tool name.
OPERATIONS: dict[str, str] = {
    "search": "search_index",
    "entity_list": "entity_list",
    "load": "entity_load_by_id",
    "field_values": "entity_field_values",
    "field_definitions": "entity_field_value_definitions",
    "stub": "entity_stub",
    "set_value": "field_set_value",
    "save": "entity_save",
}
READ_OPERATIONS = ("search", "entity_list", "load", "field_values", "field_definitions")
WRITE_OPERATIONS = ("stub", "set_value", "save")

# set_default: canvas_set_default_page_variant changes every page at once,
# immediately — live, like the homepage.
_DENIED_WORDS = ("publish", "set_homepage", "set_default", "delete", "discard")


def is_denied(tool: mcp_types.Tool) -> bool:
    """Never exposed, never mapped: publishing, the homepage and site
    defaults, deleting, discarding."""
    if any(word in tool.name.lower() for word in _DENIED_WORDS):
        return True
    return bool(tool.annotations and tool.annotations.destructive_hint)


@dataclass(frozen=True)
class McpMapping:
    search: str
    entity_list: str
    load: str
    field_values: str
    field_definitions: str
    stub: str | None = None
    set_value: str | None = None
    save: str | None = None
    # The Search API index content searches go to; None until configured.
    search_index: str | None = None
    # Extra search arguments from DREACHY_MCP_MAPPING (min_score, conjunction…).
    search_params: Mapping[str, Any] = field(default_factory=dict)

    @property
    def can_write(self) -> bool:
        return bool(self.stub and self.set_value and self.save)

    def search_args(self, query: str, *, limit: int) -> dict[str, Any]:
        args: dict[str, Any] = {"index": self.search_index, "search_words": query, "amount": limit}
        args.update(self.search_params)
        args["check_access"] = True  # last: nothing switches it off
        return args

    def list_args(
        self,
        entity_type: str,
        *,
        amount: int,
        bundle: str | None = None,
        sort: str = "created",
        order: str = "DESC",
        field: str | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"entity_type_id": entity_type}
        if bundle:
            args["bundle"] = bundle
        args.update(amount=amount, sort_field=sort, sort_order=order)
        if field:
            args["fields"] = field  # ONE name: the server ignores lists
        return args

    def load_args(self, entity_type: str, entity_id: int | str) -> dict[str, Any]:
        return {"entity_type_id": entity_type, "entity_id": int(entity_id)}

    def values_args(self, token: str, field: str | None = None) -> dict[str, Any]:
        return {"entity": token, **({"fields": field} if field else {})}

    def definitions_args(self, entity_type: str, bundle: str) -> dict[str, Any]:
        return {"entity_type_id": entity_type, "bundle": bundle}

    def stub_args(self, entity_type: str, bundle: str, base_fields: dict[str, Any]) -> dict[str, Any]:
        return {"entity_type_id": entity_type, "bundle": bundle, "base_fields": base_fields}

    def set_args(self, token: str, field: str, value: dict[str, Any]) -> dict[str, Any]:
        return {"entity": token, "field_name": field, "value": value}

    def save_args(self, token: str) -> dict[str, Any]:
        return {"entity": token}


def _by_suffix(names: Iterable[str], suffix: str) -> list[str]:
    return [name for name in names if name == suffix or name.endswith(f"_{suffix}")]


def _index_enum(tool: mcp_types.Tool | None) -> str | None:
    if tool is None:
        return None
    index = ((tool.input_schema or {}).get("properties") or {}).get("index") or {}
    choices = index.get("enum") or []
    return str(choices[0]) if choices else None


def discover_mapping(
    tools: list[mcp_types.Tool], overrides: Mapping[str, Any] | None = None, *, search_index: str = ""
) -> McpMapping:
    """The mapping for this server's *tools*. Raises DreachySiteError when a
    read operation is missing or an override can't be honoured."""
    overrides = dict(overrides or {})
    served = {tool.name: tool for tool in tools}
    chosen: dict[str, str | None] = {}
    for operation, suffix in OPERATIONS.items():
        override = overrides.get(operation)
        if override:
            if override not in served:
                raise DreachySiteError(f"the MCP mapping names {override} for {operation}, which the server doesn't offer")
            chosen[operation] = override
            continue
        matches = _by_suffix(served, suffix)
        if len(matches) > 1:
            raise DreachySiteError(
                f"several MCP tools could be {operation} ({', '.join(sorted(matches))}): name one in the MCP mapping"
            )
        chosen[operation] = matches[0] if matches else None
    for operation, name in chosen.items():
        if name and is_denied(served[name]):
            raise DreachySiteError(f"the MCP mapping can't use {name} for {operation}: Dreachy never publishes, deletes or discards")
    missing = [operation for operation in READ_OPERATIONS if not chosen[operation]]
    if missing:
        raise DreachySiteError(f"this site's MCP server lacks {', '.join(OPERATIONS[o] for o in missing)}")
    unknown = set(overrides) - set(OPERATIONS) - {"search_params"}
    if unknown:
        logger.warning("Ignoring unknown MCP mapping keys: %s", ", ".join(sorted(unknown)))
    search_params = overrides.get("search_params") or {}
    return McpMapping(
        **chosen,  # type: ignore[arg-type]
        search_index=search_index or _index_enum(served.get(chosen["search"] or "")),
        search_params=dict(search_params) if isinstance(search_params, Mapping) else {},
    )
