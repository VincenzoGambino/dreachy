"""JsonApiBackend's editorial side (R3): pending content and draft creation
against a mocked site. Notes are always unpublished — on a moderated type via
the draft moderation state, since Content Moderation forbids setting status."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from _fake_site import EDITORIAL_LABELS, EDITORIAL_NODES, FakeSite

import dreachy.backend as backend_module
from dreachy.backend import DreachySiteError


def _site(**kwargs) -> FakeSite:
    return FakeSite(EDITORIAL_NODES, labels=EDITORIAL_LABELS, **kwargs)


def _discovered(site: FakeSite):
    client = site.client()
    assert client.refresh_schema()
    return client


def test_discovery_marks_moderated_types() -> None:
    client = _discovered(_site())

    assert (client.schema["news_item"].moderated, client.schema["event"].moderated) == (True, False)


def test_pending_lists_unpublished_work_newest_first_without_archived() -> None:
    pending = _discovered(_site()).get_pending_nodes()

    assert [(n["title"], n["moderation_state"]) for n in pending] == [
        ("Park opening hours", "draft"),
        ("Budget report", "review"),
        ("Winter market", None),
    ]
    assert not any(n["status"] for n in pending)


def test_a_note_on_a_moderated_type_is_a_moderation_draft() -> None:
    site = _site()

    _discovered(site).create_draft("news_item", "Park bench", "Needs painting.")

    assert site.created[0]["data"] == {
        "type": "node--news_item",
        "attributes": {
            "title": "Park bench",
            "body": {"value": "Needs painting.", "format": "plain_text"},
            "moderation_state": "draft",
        },
    }


def test_a_note_on_an_unmoderated_type_is_unpublished() -> None:
    site = _site()

    _discovered(site).create_draft("event", "Carol concert", "In the square.")

    attributes = site.created[0]["data"]["attributes"]
    assert attributes["status"] is False
    assert "moderation_state" not in attributes
    assert attributes["field_description"] == {"value": "In the square.", "format": "plain_text"}


def test_no_note_is_written_before_discovery() -> None:
    site = _site()

    with pytest.raises(DreachySiteError):
        site.client().create_draft("news_item", "T", "B")

    assert site.created == []


def test_no_note_is_written_to_a_type_dreachy_doesnt_talk_about() -> None:
    site = _site()

    with pytest.raises(DreachySiteError):
        _discovered(site).create_draft("page", "T", "B")

    assert site.created == []


def test_a_read_only_site_says_so() -> None:
    with pytest.raises(DreachySiteError) as excinfo:
        _discovered(_site(write_status=405)).create_draft("news_item", "T", "B")

    assert "read-only" in str(excinfo.value)


def test_a_forbidden_note_says_the_site_doesnt_allow_it() -> None:
    with pytest.raises(DreachySiteError) as excinfo:
        _discovered(_site(write_status=403)).create_draft("news_item", "T", "B")

    assert "doesn't let Dreachy" in str(excinfo.value)


def test_a_note_the_site_published_is_an_error_not_a_success() -> None:
    with pytest.raises(DreachySiteError) as excinfo:
        _discovered(_site(publish_on_create=True)).create_draft("event", "T", "B")

    assert "published" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Final-review fixes
# ---------------------------------------------------------------------------


def test_pending_sees_other_peoples_drafts_despite_json_api_filter_access() -> None:
    # Core narrows any filtered node collection to published (+ own) content
    # without bypass node access, so a status filter can't see an editor's
    # drafts; an unfiltered read, checked per item, can.
    titles = [n["title"] for n in _discovered(_site(filter_hides_drafts=True)).get_pending_nodes()]

    assert titles == ["Park opening hours", "Budget report", "Winter market"]


def test_a_refused_note_carries_the_sites_reason() -> None:
    reason = "The transition from Draft to Draft is not allowed."

    with pytest.raises(DreachySiteError) as excinfo:
        _discovered(_site(write_status=422, write_detail=reason)).create_draft("news_item", "T", "B")

    assert reason in str(excinfo.value)


def test_a_status_permission_refusal_says_which_field() -> None:
    detail = "The current user is not allowed to POST the selected field (status)."

    with pytest.raises(DreachySiteError) as excinfo:
        _discovered(_site(write_status=403, write_detail=detail)).create_draft("event", "T", "B")

    assert "doesn't let Dreachy" in str(excinfo.value) and "(status)" in str(excinfo.value)


def test_a_note_after_a_failed_start_rediscovers_instead_of_refusing(monkeypatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(backend_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    site = _site(index_status=503)
    client = site.client(auto_discover=True)
    assert client.refresh_schema() is False  # the site was down at start

    site.index_status = 200
    clock[0] += 61  # past the retry interval
    client.create_draft("news_item", "Park bench", "Needs painting.")

    assert len(site.created) == 1
