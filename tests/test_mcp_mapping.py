"""The site-configurable MCP mapping (R4 Task 4), against the sandbox's
recorded tools/list (tests/fixtures/mcp/tools_list.json)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp import types as mcp_types

from dreachy.backend import DreachySiteError
from dreachy.mcp_mapping import discover_mapping, is_denied

_TOOLS = json.loads((Path(__file__).parent / "fixtures" / "mcp" / "tools_list.json").read_text())


def _tools(rename=lambda name: name) -> list[mcp_types.Tool]:
    return [mcp_types.Tool.model_validate({**tool, "name": rename(tool["name"])}) for tool in _TOOLS]


def test_discovery_maps_this_sites_tools() -> None:
    mapping = discover_mapping(_tools(), search_index="content_vector")

    assert (mapping.search, mapping.entity_list, mapping.load) == (
        "tool_api__demo_search_index",
        "tool_api__demo_entity_list",
        "tool_api__demo_entity_load_by_id",
    )
    assert (mapping.field_values, mapping.field_definitions) == (
        "tool_api__demo_entity_field_values",
        "tool_api__demo_entity_field_value_definitions",
    )
    assert (mapping.stub, mapping.set_value, mapping.save) == (
        "tool_api__demo_entity_stub",
        "tool_api__demo_field_set_value",
        "tool_api__demo_entity_save",
    )
    assert mapping.search_index == "content_vector"
    assert mapping.can_write


def test_a_differently_prefixed_site_is_mapped_too() -> None:
    mapping = discover_mapping(_tools(lambda name: name.replace("tool_api__demo_", "mysite_")))

    assert mapping.load == "mysite_entity_load_by_id"
    assert mapping.field_values == "mysite_entity_field_values"


def test_overrides_win_over_discovery() -> None:
    tools = _tools() + [mcp_types.Tool(name="custom_search", inputSchema={"type": "object"})]

    mapping = discover_mapping(tools, {"search": "custom_search"})

    assert mapping.search == "custom_search"


def test_an_override_naming_a_tool_the_server_lacks_is_a_site_error() -> None:
    with pytest.raises(DreachySiteError, match="no_such_tool"):
        discover_mapping(_tools(), {"load": "no_such_tool"})


def test_an_override_can_never_point_at_a_denied_tool() -> None:
    with pytest.raises(DreachySiteError, match="publish"):
        discover_mapping(_tools(), {"save": "tool_api__canvas_publish_auto_saves"})


def test_a_missing_operation_is_a_clear_site_error() -> None:
    tools = [tool for tool in _tools() if not tool.name.endswith("entity_list")]

    with pytest.raises(DreachySiteError, match="entity_list"):
        discover_mapping(tools)


def test_without_the_write_tools_the_mapping_is_read_only() -> None:
    tools = [tool for tool in _tools() if not tool.name.endswith(("entity_stub", "entity_save"))]

    mapping = discover_mapping(tools)

    assert not mapping.can_write
    assert mapping.stub is None


def test_check_access_cannot_be_overridden() -> None:
    mapping = discover_mapping(
        _tools(), {"search_params": {"check_access": False, "min_score": 0.4}}, search_index="content_vector"
    )

    args = mapping.search_args("admissions", limit=5)

    assert args["check_access"] is True
    assert args == {
        "index": "content_vector",
        "search_words": "admissions",
        "amount": 5,
        "min_score": 0.4,
        "check_access": True,
    }


def test_argument_builders_use_the_served_names() -> None:
    mapping = discover_mapping(_tools())

    assert mapping.list_args("node", amount=20, sort="changed", field="status") == {
        "entity_type_id": "node",
        "amount": 20,
        "sort_field": "changed",
        "sort_order": "DESC",
        "fields": "status",
    }
    assert mapping.list_args("node", bundle="news", amount=0) == {
        "entity_type_id": "node",
        "bundle": "news",
        "amount": 0,
        "sort_field": "created",
        "sort_order": "DESC",
    }
    assert mapping.load_args("node", "142") == {"entity_type_id": "node", "entity_id": 142}
    assert mapping.values_args("{{entity:1}}") == {"entity": "{{entity:1}}"}
    assert mapping.values_args("{{entity:1}}", "status") == {"entity": "{{entity:1}}", "fields": "status"}
    assert mapping.definitions_args("node", "news") == {"entity_type_id": "node", "bundle": "news"}
    assert mapping.stub_args("node", "news", {"title": "x"}) == {
        "entity_type_id": "node",
        "bundle": "news",
        "base_fields": {"title": "x"},
    }
    assert mapping.set_args("{{entity:1}}", "description", {"value": "x"}) == {
        "entity": "{{entity:1}}",
        "field_name": "description",
        "value": {"value": "x"},
    }
    assert mapping.save_args("{{entity:1}}") == {"entity": "{{entity:1}}"}


def test_the_search_index_comes_from_a_schema_enum_when_there_is_one() -> None:
    def with_enum(tool: dict) -> dict:
        if not tool["name"].endswith("search_index"):
            return tool
        schema = json.loads(json.dumps(tool["inputSchema"]))
        schema["properties"]["index"]["enum"] = ["site_content", "images"]
        return {**tool, "inputSchema": schema}

    tools = [mcp_types.Tool.model_validate(with_enum(tool)) for tool in _TOOLS]

    assert discover_mapping(tools).search_index == "site_content"
    assert discover_mapping(tools, search_index="images").search_index == "images"


def test_no_search_index_means_none_not_a_guess() -> None:
    assert discover_mapping(_tools()).search_index is None


@pytest.mark.parametrize(
    "name",
    [
        "tool_api__canvas_publish_auto_saves",
        "tool_api__canvas_set_homepage",
        "tool_api__canvas_delete_page",
        "tool_api__canvas_discard_auto_save",
        "tool_api__demo_unpublish",
    ],
)
def test_publish_homepage_delete_and_discard_are_denied_by_name(name: str) -> None:
    assert is_denied(mcp_types.Tool(name=name, inputSchema={"type": "object"}))


def test_a_destructive_tool_is_denied_whatever_its_name() -> None:
    tool = mcp_types.Tool(
        name="tool_api__canvas_tidy", inputSchema={"type": "object"}, annotations={"destructiveHint": True}
    )

    assert is_denied(tool)
    assert not is_denied(mcp_types.Tool(name="tool_api__canvas_add_component", inputSchema={"type": "object"}))


def test_every_recorded_destructive_tool_is_denied() -> None:
    recorded = {tool.name for tool in _tools() if tool.annotations and tool.annotations.destructive_hint}

    assert recorded and all(is_denied(tool) for tool in _tools() if tool.name in recorded)
