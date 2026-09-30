"""The tool specs the conversation app sends to the LLM name the site's own
content types. Specs are built once at start (after main._warm_schema), so
these read the shared client's schema, never the network."""

from __future__ import annotations

import pytest

import dreachy.tools._shared as shared
from dreachy.client import DrupalClient
from dreachy.config import Config
from dreachy.tools._shared import or_list
from dreachy.tools.drupal_find_content import DrupalFindContent
from dreachy.tools.drupal_read_article import DrupalReadArticle


@pytest.fixture(autouse=True)
def _reset_shared_client():
    shared._client = None
    yield
    shared._client = None


def _use(config: Config) -> None:
    shared._client = DrupalClient(config)


def test_find_content_spec_is_unchanged_on_umami() -> None:
    _use(Config())

    spec = DrupalFindContent().spec()

    assert spec["parameters"]["properties"]["content_type"]["enum"] == ["article", "page", "recipe"]
    assert spec["description"] == (
        "Search the site's content by keyword, optionally filtered to one content type "
        "(article, page, or recipe). Use when asked to find, search, or look up something "
        "on the site."
    )


def test_find_content_offers_the_sites_own_types() -> None:
    _use(Config(content_types=("news_item", "event")))

    spec = DrupalFindContent().spec()

    assert spec["parameters"]["properties"]["content_type"]["enum"] == ["news_item", "event"]
    assert "(news_item or event)" in spec["description"]


def test_find_content_offers_only_the_enabled_types() -> None:
    _use(Config(enabled_types=("recipe",)))

    assert DrupalFindContent().spec()["parameters"]["properties"]["content_type"]["enum"] == ["recipe"]


def test_read_article_names_the_sites_types() -> None:
    _use(Config())

    assert DrupalReadArticle().spec()["description"].startswith(
        "Fetch the full text of one article, page, or recipe by title or URL path, for reading aloud."
    )


def test_or_list_reads_naturally() -> None:
    assert or_list(["news_item"]) == "news_item"
    assert or_list(["a", "b"]) == "a or b"
    assert or_list(["a", "b", "c"]) == "a, b, or c"
