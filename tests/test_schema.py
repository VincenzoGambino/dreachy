"""Unit tests for schema.py's content-model heuristics. Pure: no HTTP."""

from __future__ import annotations

import logging

from _fake_site import NEWS_LABELS, NEWS_NODES, UMAMI_LABELS, UMAMI_NODES, formatted

from dreachy.schema import (
    FALLBACK_TYPES,
    TypeSchema,
    build_schema,
    fallback_schema,
    humanize,
    infer_type_schema,
    select_types,
)


def _samples(nodes: dict) -> dict:
    return {bundle: [r["attributes"] for r in resources] for bundle, resources in nodes.items()}


def test_umami_mapping_falls_out_of_the_heuristics() -> None:
    assert build_schema(UMAMI_NODES, UMAMI_LABELS, _samples(UMAMI_NODES)) == FALLBACK_TYPES


def test_a_non_umami_model_needs_no_python_edits() -> None:
    assert build_schema(NEWS_NODES, NEWS_LABELS, _samples(NEWS_NODES)) == {
        "event": TypeSchema("Event", "title", ("field_description",), "field_teaser"),
        "news_item": TypeSchema("News item", "title", ("body", "field_sidebar")),
    }


def test_schema_is_ordered_by_machine_name() -> None:
    assert list(build_schema(NEWS_NODES, NEWS_LABELS, _samples(NEWS_NODES))) == ["event", "news_item"]


def test_body_is_preferred_over_field_body_and_other_long_text() -> None:
    attributes = {"field_notes": formatted("n"), "field_body": formatted("fb"), "body": formatted("b")}

    assert infer_type_schema("X", [attributes]).text_fields == ("body", "field_body", "field_notes")


def test_a_field_empty_on_the_newest_node_is_still_found_on_older_ones() -> None:
    newest = {"title": "t", "body": None}
    older = {"title": "t", "body": formatted("<p>text</p>")}

    assert infer_type_schema("X", [newest, older]).text_fields == ("body",)


def test_a_type_with_no_formatted_text_is_skipped() -> None:
    assert infer_type_schema("Gallery", [{"title": "t", "field_photo_count": 12, "field_tags": ["a"]}]) is None


def test_plain_strings_and_link_objects_are_not_text() -> None:
    attributes = {
        "field_subtitle": "plain",
        "path": {"alias": "/x", "pid": 1, "langcode": "en"},
        "field_link": {"uri": "https://example.com", "title": "x"},
    }

    assert infer_type_schema("X", [attributes]) is None


def test_a_lone_teaser_named_field_is_used_as_the_body() -> None:
    assert infer_type_schema("X", [{"field_summary": formatted("s")}]) == TypeSchema("X", "title", ("field_summary",))


def test_a_type_with_no_content_yet_is_kept_with_guessed_fields() -> None:
    schema = build_schema(["news_item", "recipe"], {}, {"news_item": [], "recipe": []})

    assert schema["news_item"] == TypeSchema("News item", "title", ("body", "field_body"))
    # An Umami type keeps its known mapping, plus the common names in case this isn't Umami.
    assert schema["recipe"] == TypeSchema(
        "Recipe", "title", ("field_recipe_instruction", "body", "field_body"), "field_summary"
    )


def test_a_type_that_could_not_be_read_is_skipped() -> None:
    samples = {"article": _samples(UMAMI_NODES)["article"], "secret": None}

    assert list(build_schema(["article", "secret"], {}, samples)) == ["article"]


def test_missing_labels_are_derived_from_the_machine_name() -> None:
    assert humanize("news_item") == "News item"
    assert build_schema(NEWS_NODES, {}, _samples(NEWS_NODES))["news_item"].label == "News item"


def test_fallback_schema_is_the_pre_discovery_umami_table() -> None:
    assert fallback_schema(("article", "page", "recipe")) == FALLBACK_TYPES


def test_an_empty_selection_means_every_type() -> None:
    assert select_types(FALLBACK_TYPES, ()) == FALLBACK_TYPES


def test_a_selection_keeps_schema_order() -> None:
    assert list(select_types(FALLBACK_TYPES, ("recipe", "article"))) == ["article", "recipe"]


def test_a_selection_of_only_vanished_types_falls_back_to_every_type() -> None:
    assert select_types(FALLBACK_TYPES, ("news_item",)) == FALLBACK_TYPES


def test_a_type_skipped_for_having_no_text_is_logged(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="dreachy.schema"):
        build_schema(NEWS_NODES, NEWS_LABELS, _samples(NEWS_NODES))

    assert "node--gallery" in caplog.text


def test_a_type_whose_content_carries_a_moderation_state_is_moderated() -> None:
    samples = [{"title": "t", "body": formatted("b"), "moderation_state": "published"}]

    assert infer_type_schema("News", samples).moderated is True


def test_a_type_without_moderation_state_is_not_moderated() -> None:
    assert infer_type_schema("News", [{"title": "t", "body": formatted("b")}]).moderated is False
