"""Fixture sites for content-model discovery tests.

Imported as a plain module (pytest's default import mode puts tests/ on
sys.path). Shaped like core JSON:API output: formatted text fields are
objects with value/format/processed; plain strings, numbers, lists and link
objects sit alongside them and must not be mistaken for text.
"""

from __future__ import annotations

from typing import Any


def formatted(html: str) -> dict[str, str]:
    """A formatted text field value as core JSON:API serialises it."""
    return {"value": html, "format": "basic_html", "processed": html}


def node(bundle: str, uuid: str, title: str, created: str, **fields: Any) -> dict[str, Any]:
    return {
        "type": f"node--{bundle}",
        "id": uuid,
        "attributes": {
            "drupal_internal__nid": 1,
            "langcode": "en",
            "status": True,
            "title": title,
            "created": created,
            "changed": created,
            "path": {"alias": f"/{bundle}/{uuid}", "pid": 1, "langcode": "en"},
            **fields,
        },
    }


# The Umami demo profile, as confirmed against the live site 2026-07-27.
UMAMI_LABELS = {"article": "Article", "page": "Basic page", "recipe": "Recipe"}
UMAMI_NODES = {
    "article": [
        node(
            "article", "a1", "Give your oatmeal the ultimate makeover", "2026-07-20T10:00:00+00:00",
            field_body=formatted("<p>Oatmeal is <strong>great</strong> for breakfast.</p>"),
        ),
    ],
    "page": [
        node(
            "page", "p1", "About Umami", "2026-07-15T08:00:00+00:00",
            field_body=formatted("<p>Umami is a fictional food magazine.</p>"),
        ),
    ],
    "recipe": [
        node(
            "recipe", "r1", "Borscht with pork ribs", "2026-07-25T12:00:00+00:00",
            field_cooking_time=60,
            field_difficulty="medium",
            field_ingredients=["1 kg pork ribs", "2 beetroots"],
            field_number_of_servings=6,
            field_preparation_time=20,
            field_recipe_instruction=formatted("<ol><li>Cook the ribs.</li></ol>"),
            field_summary=formatted("<p>A hearty Ukrainian soup.</p>"),
        ),
    ],
}

# An invented non-Umami model: core's standard body field (plus a second
# long-text field), a type whose teaser is its own field — listed first, so
# order alone can't pick the body — and a type with no text at all.
NEWS_LABELS = {"news_item": "News item", "event": "Event", "gallery": "Gallery"}
NEWS_NODES = {
    "news_item": [
        node(
            "news_item", "n2", "Council approves new park", "2026-09-28T09:00:00+00:00",
            body=formatted("<p>The council voted to build a park.</p>"),
            field_sidebar=formatted("<p>Related: parks map</p>"),
        ),
        node(
            "news_item", "n1", "Library reopens", "2026-09-20T09:00:00+00:00",
            body=formatted("<p>The library is open again.</p>"),
            field_sidebar=None,
        ),
    ],
    "event": [
        node(
            "event", "e1", "Harvest fair", "2026-09-25T09:00:00+00:00",
            field_teaser=formatted("<p>Fun for all ages.</p>"),
            field_description=formatted("<p>Stalls, music and a tractor parade.</p>"),
            field_date="2026-10-04",
        ),
    ],
    "gallery": [
        node("gallery", "g1", "Summer photos", "2026-08-01T09:00:00+00:00", field_photo_count=12),
    ],
}
