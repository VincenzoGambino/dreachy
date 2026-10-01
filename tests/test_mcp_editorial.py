"""McpBackend's editorial side (R4 Task 6): pending, create_draft, can_edit.

The draft guarantee is Dreachy's, never the server's (Ruling B): the stub's
state is checked BEFORE saving — set to draft if it isn't, refused with
nothing saved if that fails — and the saved node is re-read after. Required
text fields get the note's text; nothing else is ever invented (Ruling C)."""

from __future__ import annotations

import pytest
from _fake_mcp import FakeEntityStore, FakeMcpSite, FakeTokenEndpoint
from test_mcp_backend import DEFINITIONS, JAN_2025, NEWEST, SEP_2026, _node

from dreachy.backend import DreachySiteError
from dreachy.config import Config
from dreachy.mcp_backend import McpBackend
from dreachy.schema import TypeSchema

EDITORIAL = {"default": "draft", "draft_to_draft": True}
UNMODERATED = {"field_definitions": {"description": {"type": "text_long", "required": False}}}


def _store(**workflows) -> FakeEntityStore:
    return FakeEntityStore(
        {
            5: _node("standard_page", "Admissions", JAN_2025, description="<p>Apply.</p>", preview_text="x"),
            6: _node("news", "Budget report", SEP_2026, status="0", state="draft", description="<p>Budget.</p>"),
            7: _node("news", "Old notice", NEWEST, status="0", state="archived", description="<p>Gone.</p>"),
            8: _node("student_announcement", "Winter market", NEWEST, status="0", state=None, description="<p>Stalls.</p>"),
            9: _node("news", "Park opening hours", "1790600000", status="0", state="draft", description="<p>Hours.</p>"),
        },
        definitions={**DEFINITIONS, "student_announcement": UNMODERATED},
        workflows={"standard_page": EDITORIAL, "news": EDITORIAL, **workflows},
    )


@pytest.fixture
def make():
    backends: list[McpBackend] = []

    def build(store: FakeEntityStore | None = None, *, discover: bool = True, tools=None, **settings):
        store = store or _store()
        site = FakeMcpSite(tools if tools is not None else store.tools())
        config = Config(
            base_url="https://site.test",
            auth=settings.pop("auth", "oauth"),
            oauth_client_id="dreachy",
            oauth_client_secret="s",
            backend="mcp",
            mcp_search_index="content_vector",
            **settings,
        )
        backend = McpBackend(config, http_client_factory=site.client_factory(), token_http_client=FakeTokenEndpoint().client())
        backends.append(backend)
        if discover:
            assert backend.refresh_schema()
        return backend, site, store

    yield build
    for backend in backends:
        backend.close()


def _names(site: FakeMcpSite, since: int = 0) -> list[str]:
    return [name.removeprefix("tool_api__demo_") for _, name, _ in site.calls[since:]]


# -- create_draft ----------------------------------------------------------


def test_a_note_over_mcp_is_draft_only_and_verified_after_save(make) -> None:
    backend, site, store = make()
    before = len(site.calls)

    node = backend.create_draft("standard_page", "Park bench", "Needs painting.")

    assert (node["title"], node["status"], node["moderation_state"]) == ("Park bench", False, "draft")
    saved = store.saves[-1]["fields"]
    assert (saved["description"], saved["preview_text"]) == ("Needs painting.", "Needs painting.")
    assert _names(site, before) == [
        "entity_stub",
        "entity_field_values",  # the pre-save check: moderation_state
        "field_set_value",  # the body
        "field_set_value",  # preview_text: required text
        "entity_save",
        "entity_load_by_id",  # verified after save
        "entity_field_values",
    ]


def test_the_note_text_goes_in_as_plain_text(make) -> None:
    backend, site, _ = make()

    backend.create_draft("standard_page", "Park bench", "Needs painting.")

    body_set = next(args for _, name, args in site.calls if name.endswith("field_set_value") and args["field_name"] == "description")
    assert body_set["value"] == {"value": "Needs painting.", "format": "plain_text"}
    stub = next(args for _, name, args in site.calls if name.endswith("entity_stub"))
    assert stub["base_fields"] == {"title": "Park bench", "status": False}


def test_a_workflow_that_doesnt_start_as_draft_is_set_to_draft_before_save(make) -> None:
    backend, site, store = make(_store(standard_page={"default": "published"}))

    node = backend.create_draft("standard_page", "Park bench", "Needs painting.")

    assert node["moderation_state"] == "draft"
    assert not node["status"]
    sets = [args["field_name"] for _, name, args in site.calls if name.endswith("field_set_value")]
    assert sets[0] == "moderation_state"


def test_if_draft_cant_be_set_nothing_is_saved(make) -> None:
    backend, _, store = make(_store(standard_page={"default": "published", "refuse_draft": True}))

    with pytest.raises(DreachySiteError, match="nothing was saved"):
        backend.create_draft("standard_page", "Park bench", "Needs painting.")

    assert store.saves == []


def test_a_workflow_without_draft_to_draft_is_a_clear_refusal(make) -> None:
    # The sandbox's article: its new stubs are drafts, but saving one fails.
    backend, _, store = make(_store(standard_page={"default": "draft", "draft_to_draft": False}))

    with pytest.raises(DreachySiteError) as excinfo:
        backend.create_draft("standard_page", "Park bench", "Needs painting.")

    assert "Invalid state transition from Draft to Draft" in str(excinfo.value)
    assert store.saves == []


def test_an_unmoderated_type_is_saved_unpublished(make) -> None:
    backend, _, store = make()

    node = backend.create_draft("student_announcement", "Bike sale", "Saturday.")

    assert node["status"] is False
    assert node["moderation_state"] is None
    assert store.saves[-1]["fields"]["status"] == "0"


def test_a_note_the_site_published_over_mcp_is_an_error(make) -> None:
    backend, _, store = make()
    store.publishes_everything = True

    with pytest.raises(DreachySiteError, match="published"):
        backend.create_draft("standard_page", "Park bench", "Needs painting.")


def test_a_chain_broken_before_save_writes_nothing(make) -> None:
    backend, _, store = make()
    store.refused_fields = {"preview_text"}

    with pytest.raises(DreachySiteError):
        backend.create_draft("standard_page", "Park bench", "Needs painting.")

    assert store.saves == []


def test_a_required_field_dreachy_cant_fill_is_named_in_the_refusal(make) -> None:
    backend, _, store = make()

    with pytest.raises(DreachySiteError) as excinfo:
        backend.create_draft("news", "Park bench", "Needs painting.")

    assert "news_publish_date" in str(excinfo.value)
    assert store.saves == []


def test_no_mcp_write_before_discovery(make) -> None:
    backend, site, _ = make(discover=False)

    with pytest.raises(DreachySiteError):
        backend.create_draft("standard_page", "Park bench", "Needs painting.")

    assert "entity_stub" not in _names(site)


def test_notes_only_go_to_enabled_types(make) -> None:
    backend, site, _ = make(enabled_types=("news",))

    with pytest.raises(DreachySiteError):
        backend.create_draft("standard_page", "Park bench", "Needs painting.")

    assert "entity_stub" not in _names(site)


def test_a_write_gets_a_session_of_its_own(make) -> None:
    backend, site, _ = make()
    backend.get_recent_nodes(5)
    read_session = site.session_ids()[-1]

    backend.create_draft("standard_page", "Park bench", "Needs painting.")

    write_sessions = {sid for sid, name, _ in site.calls if name.endswith(("entity_stub", "entity_save"))}
    assert write_sessions and read_session not in write_sessions
    assert set(site.sessions) == {read_session}  # the write session was closed


# -- can_edit --------------------------------------------------------------


def test_can_edit_needs_the_login_and_the_write_tools(make) -> None:
    store = _store()
    read_only = {name: tool for name, tool in store.tools().items() if name not in store.write_tools()}

    assert make(store)[0].can_edit()
    assert not make(store, tools=read_only)[0].can_edit()
    assert not make(store, discover=False, auth="none")[0].can_edit()


def test_can_edit_is_false_when_the_site_is_down(make) -> None:
    backend, site, _ = make(discover=False)
    site.down = True

    assert not backend.can_edit()


# -- pending ---------------------------------------------------------------


def test_pending_lists_unpublished_work_newest_change_first_without_archived(make) -> None:
    backend, _, _ = make()

    pending = backend.get_pending_nodes()

    assert [(n["title"], n["moderation_state"]) for n in pending] == [
        ("Park opening hours", "draft"),
        ("Winter market", None),
        ("Budget report", "draft"),
    ]
    assert not any(n["status"] for n in pending)


def test_pending_looks_at_one_window_of_about_twenty(make) -> None:
    backend, site, _ = make()
    before = len(site.calls)

    backend.get_pending_nodes()

    calls = site.calls[before:]
    assert {name.removeprefix("tool_api__demo_") for _, name, _ in calls} == {"entity_list"}
    assert {(args["amount"], args["sort_field"]) for _, _, args in calls} == {(20, "changed")}


# -- the note type (Ruling C) ----------------------------------------------


def test_the_default_note_type_has_the_fewest_extra_required_fields(make) -> None:
    backend, _, _ = make()

    # news: preview_text + ai_automator_status + news_publish_date; standard_page:
    # preview_text + ai_automator_status; student_announcement: none.
    assert backend.note_type() == "student_announcement"
    assert make(enabled_types=("news", "standard_page"))[0].note_type() == "standard_page"
    assert make(note_type="news")[0].note_type() == "news"


def test_over_json_api_the_default_note_type_is_still_the_first_enabled_one() -> None:
    from _fake_backend import FakeBackend

    backend = FakeBackend([], Config(content_types=("recipe", "article")))
    backend._full_schema = {  # noqa: SLF001
        "recipe": TypeSchema("Recipe", "title", ("field_recipe_instruction",)),
        "article": TypeSchema("Article", "title", ("field_body",)),
    }

    assert backend.note_type() == "recipe"
