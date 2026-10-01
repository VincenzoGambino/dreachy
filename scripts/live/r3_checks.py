"""Laptop-side live checks for R3, against a local DDEV site prepared by
scripts/live/ddev-r3-setup.sh (see docs/live-checks-r3.md).

Reads the DREACHY_* settings from the environment, as the app does, and
calls the real tools the way the conversation app does — only the robot and
the voice are missing. Creates one unpublished draft on the site, titled
"Dreachy live check <time>"; nothing is published.

Exit status: 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime

import dreachy.tools._shared as shared
from dreachy import main as dreachy_main
from dreachy.client import JsonApiBackend
from dreachy.config import Config
from dreachy.tools.drupal_create_note import DrupalCreateNote
from dreachy.tools.drupal_pending_content import DrupalPendingContent
from dreachy.tools.drupal_read_article import DrupalReadArticle
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies

OTHER_EDITORS_DRAFT = "Draft: park opening hours"  # created by the setup script, author: admin
INJECTION_TITLE = "A note to the robot"
INJECTION_MARKER = "Ignore all previous instructions"

_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def call(tool, **kwargs) -> dict:
    return asyncio.run(tool(ToolDependencies(reachy_mini=None, movement_manager=None), **kwargs))


def pending_titles(backend: JsonApiBackend) -> dict[str, dict]:
    # Unfiltered read: the only way to see drafts (core JSON:API narrows any
    # *filtered* node list to published + own content without bypass).
    return {node["title"]: node for node in backend.get_pending_nodes()}


def main() -> int:
    config = Config.from_env()
    if not config.uses_oauth:
        print("Set the DREACHY_* login variables first (scripts/live/ddev-r3-setup.sh prints them).")
        return 2
    print(f"Site: {config.base_url} (locale {config.default_locale!r}), note type {config.note_type or '(first)'}\n")

    backend = JsonApiBackend(config, auto_discover=True)
    shared._client = backend

    # L1 — the login works, so editing would be switched on at start.
    check("L1 login: the site grants Dreachy a token", backend.can_edit())
    check("L1 registration: start-up would add the editorial tools", dreachy_main._editorial_available())

    # L2 — discovery sees the moderated note type.
    discovered = backend.refresh_schema()
    note_type = config.note_type or next(iter(backend.schema))
    type_schema = backend.schema.get(note_type)
    check("L2 discovery succeeds", discovered, backend.last_discovery_problem or "")
    check(f"L2 '{note_type}' is moderated", bool(type_schema and type_schema.moderated))

    # L3 — pending sees another editor's draft.
    summary = call(DrupalPendingContent())
    titles = [item["title"] for item in summary.get("latest", [])]
    check(
        "L3 pending lists another editor's draft",
        OTHER_EDITORS_DRAFT in pending_titles(backend),
        f"count {summary.get('count')}, by state {summary.get('by_state')}, latest {titles}",
    )

    # L4 — without confirmed=true nothing is written.
    title = f"Dreachy live check {datetime.now():%H:%M:%S}"
    refused = call(DrupalCreateNote(), title=title, body="Should not be saved.")
    check("L4 a note without confirmed=true is refused", "error" in refused, refused.get("error", "")[:80])
    refused_str = call(DrupalCreateNote(), title=title, body="Should not be saved.", confirmed="true")
    check('L4 confirmed="true" (a string) is refused too', "error" in refused_str)
    check("L4 ...and nothing was saved", title not in pending_titles(backend))

    # L5 — with confirmed=true: saved as an unpublished draft.
    saved = call(DrupalCreateNote(), title=title, body="Saved by the R3 live checks; safe to delete.", confirmed=True)
    check("L5 a confirmed note is saved", saved.get("saved") == "draft", str(saved))
    note = pending_titles(backend).get(title)
    check(
        "L5 ...unpublished, in the draft moderation state",
        bool(note) and note["status"] is False and note["moderation_state"] == "draft",
        f"status {note and note['status']}, state {note and note['moderation_state']}",
    )

    # L6 — the injection article is read as data.
    read = call(DrupalReadArticle(), title_or_path=INJECTION_TITLE)
    check("L6 the injection article reads back verbatim", INJECTION_MARKER in read.get("text", ""), read.get("error", ""))
    check("L6 ...and no 'Site closed' draft exists", "Site closed" not in pending_titles(backend))

    # L7 — with the login set to None, editing is off and the tools refuse.
    anonymous = JsonApiBackend(Config(base_url=config.base_url, default_locale=config.default_locale))
    shared._client = anonymous
    check("L7 login None: start-up would leave editing off", not dreachy_main._editorial_available())
    check(
        "L7 login None: both tools refuse",
        call(DrupalPendingContent()) == {"error": "Editing isn't available on this site."}
        and call(DrupalCreateNote(), title="x", body="y", confirmed=True)
        == {"error": "Editing isn't available on this site."},
    )

    print(f"\n{sum(_results)}/{len(_results)} passed.")
    print(f'Author check (laptop): ddev drush sql:query "SELECT n.title, u.name FROM node_field_data n '
          f"JOIN users_field_data u ON u.uid = n.uid WHERE n.title = '{title}'\"  → expect: dreachy")
    return 0 if all(_results) else 1


if __name__ == "__main__":
    sys.exit(main())
