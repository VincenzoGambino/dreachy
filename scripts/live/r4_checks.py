"""Laptop-side live checks for R4, against the DrupalForge MCP sandbox.

Reads the DREACHY_* settings from the instance .env as the app does, then, in
this process only: DREACHY_BACKEND=mcp, a blank language prefix (the sandbox
serves /jsonapi unprefixed), DREACHY_MCP_SEARCH_INDEX=content_vector unless
set, and a test allowlist of site actions that includes denied tools. Calls
the real tools the way the conversation app does; only the robot and the
voice are missing.

Writes: ONE unpublished draft, titled "Dreachy live check <time>", saved as
the default note type. The configured note type is tried first; a refusal
there is expected to save nothing. Nothing is ever published.

Usage (from dreachy-app/): uv run python scripts/live/r4_checks.py
Exit status: 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path.home() / ".local" / "share" / "dreachy" / ".env")
CONFIGURED_NOTE_TYPE = os.environ.get("DREACHY_NOTE_TYPE", "")
os.environ.update(
    DREACHY_BACKEND="mcp",
    DREACHY_LOCALE="",
    DREACHY_MCP_EXTRA_TOOLS=",".join(
        (
            "tool_api__canvas_list_targets",  # allowed
            "tool_api__canvas_publish_auto_saves",  # denied: publishes
            "tool_api__canvas_set_homepage",  # denied: homepage
            "tool_api__canvas_delete_page",  # denied: deletes
            "tool_api__canvas_set_default_page_variant",  # denied: site default
            "tool_api__demo_entity_save",  # denied: entity write
        )
    ),
)
os.environ.setdefault("DREACHY_MCP_SEARCH_INDEX", "content_vector")

import dreachy.tools._shared as shared  # noqa: E402 — after the settings above
from dreachy import main as dreachy_main  # noqa: E402
from dreachy.client import JsonApiBackend  # noqa: E402
from dreachy.config import Config  # noqa: E402
from dreachy.mcp_backend import McpBackend  # noqa: E402
from dreachy.tools.drupal_create_note import DrupalCreateNote  # noqa: E402
from dreachy.tools.drupal_find_content import DrupalFindContent  # noqa: E402
from dreachy.tools.drupal_pending_content import DrupalPendingContent  # noqa: E402
from dreachy.tools.drupal_read_article import DrupalReadArticle  # noqa: E402
from dreachy.tools.drupal_site_action import DrupalSiteAction  # noqa: E402
from dreachy.tools.drupal_site_pulse import DrupalSitePulse  # noqa: E402
from dreachy.tools.drupal_whats_new import DrupalWhatsNew  # noqa: E402
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies  # noqa: E402

QUESTION = "how do I apply"  # the demo contrast: same words, both backends
_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def timed(fn, *args, **kwargs):
    started = time.monotonic()
    result = fn(*args, **kwargs)
    return result, time.monotonic() - started


def call(tool, **kwargs) -> dict:
    return asyncio.run(tool(ToolDependencies(reachy_mini=None, movement_manager=None), **kwargs))


def node_count(backend: McpBackend) -> int:
    async def chain(caller, mapping):
        _, message = await caller.call_with_message(mapping.entity_list, mapping.list_args("node", amount=1))
        return message

    message = backend._read(chain)  # noqa: SLF001 — the server's own total
    return int(message.rsplit("total", 1)[1].split(".")[0].strip())


def main() -> int:
    config = Config.from_env()
    if not config.uses_oauth:
        print("Set the DREACHY_* login variables first.")
        return 2
    print(f"Site: {config.base_url} — MCP at {config.effective_mcp_endpoint}, index {config.mcp_search_index!r}\n")

    backend = McpBackend(config, auto_discover=True)
    shared._client = backend

    # M1 — login, editing and site actions decided as at start.
    editable, seconds = timed(backend.can_edit)
    check("M1 login: token granted and the write tools are offered", editable, f"{seconds:.1f}s (first session)")
    check("M1 registration: start-up would add the editorial tools", dreachy_main._editorial_available())
    check("M1 registration: start-up would add drupal_site_action", dreachy_main._site_actions_available())

    # M2 — discovery.
    discovered, seconds = timed(backend.refresh_schema)
    schema = backend.full_schema
    check("M2 discovery succeeds", discovered, f"{len(schema)} types in {seconds:.1f}s: {', '.join(schema)}")
    page = schema.get("standard_page")
    check(
        "M2 text fields picked by type",
        bool(page and page.text_fields[0] == "description"),
        f"standard_page body {page.text_fields if page else None}, required text {page.required_text if page else None}, "
        f"required other {page.required_other if page else None}",
    )

    # M3 — what's new, the pulse, and the watcher's poll.
    news, seconds = timed(call, DrupalWhatsNew())
    check("M3 what's new", bool(news.get("items")), f"{seconds:.1f}s: {[(i['title'], i['age']) for i in news.get('items', [])]}")
    latest, seconds = timed(backend.latest_created)
    check("M3 the watcher's poll is quick", latest is not None and seconds < 5, f"{seconds:.1f}s, newest {latest}")
    pulse, seconds = timed(call, DrupalSitePulse())
    check("M3 site pulse", "node_count" in pulse, f"{seconds:.1f}s: {pulse}")

    # M4 — find, by meaning; and the same words over JSON:API.
    found, seconds = timed(call, DrupalFindContent(), keyword=QUESTION)
    titles = [m["title"] for m in found.get("matches", [])]
    check("M4 semantic find answers a question", bool(titles), f"{seconds:.1f}s: {titles}")
    jsonapi = JsonApiBackend(Config.from_env())
    try:
        jsonapi.refresh_schema()
        keyword = [n["title"] for n in jsonapi.find_content(QUESTION)]
        word = [n["title"] for n in jsonapi.find_content("apply")]
    finally:
        jsonapi.close()
    print(f"      contrast — JSON:API title search for {QUESTION!r}: {keyword}; for 'apply': {word}")

    # M5 — read aloud: by title, by path, and never just the nearest match.
    article, seconds = timed(call, DrupalReadArticle(), title_or_path="Admissions")
    check("M5 read by title", article.get("title") == "Admissions", f"{seconds:.1f}s, {len(article.get('text', ''))} chars")
    by_path, seconds = timed(call, DrupalReadArticle(), title_or_path="/standard-page/admissions")
    check("M5 a path is searched as words", by_path.get("title") == "Admissions", f"{seconds:.1f}s → {by_path.get('title')}")
    nonsense = call(DrupalReadArticle(), title_or_path="Quantum pizza regulations")
    check("M5 nothing matching reads nothing", "error" in nonsense, str(nonsense)[:120])

    # M6 — notes. The configured type first, then the default one.
    before = node_count(backend)
    title = f"Dreachy live check {datetime.now():%H:%M:%S}"
    unconfirmed = call(DrupalCreateNote(), title=title, body="Checking R4.", confirmed="true")
    check("M6 an unconfirmed note is refused", "Not saved" in unconfirmed.get("error", ""))
    if CONFIGURED_NOTE_TYPE:
        backend.config.note_type = CONFIGURED_NOTE_TYPE
        configured = call(DrupalCreateNote(), title=title, body="Checking R4.", confirmed=True)
        print(f"      configured note type {CONFIGURED_NOTE_TYPE!r}: {configured}")
        backend.config.note_type = ""
    check("M6 nothing saved so far", node_count(backend) == before, f"{before} nodes")
    note_type = backend.note_type()
    saved, seconds = timed(call, DrupalCreateNote(), title=title, body="Saved over MCP by Dreachy's R4 live check. Safe to delete.", confirmed=True)
    check(f"M6 a confirmed note is saved as a draft ({note_type})", saved.get("saved") == "draft", f"{seconds:.1f}s: {saved}")
    check("M6 exactly one node more", node_count(backend) == before + 1)

    # M7 — pending sees it.
    pending, seconds = timed(call, DrupalPendingContent())
    latest = [item["title"] for item in pending.get("latest", [])]
    check("M7 pending lists the new draft", title in latest, f"{seconds:.1f}s: count {pending.get('count')}, {pending.get('by_state')}")

    # M8 — site actions: allowlist and deny-list.
    actions = list(backend.site_actions())
    check("M8 only the allowed extra is offered", actions == ["tool_api__canvas_list_targets"], str(actions))
    asked = call(DrupalSiteAction(), action="tool_api__canvas_list_targets", arguments={"target_type": "page", "limit": 3})
    check("M8 an extra that may write asks first", "shall I" in asked.get("error", ""))
    done, seconds = timed(call, DrupalSiteAction(), action="tool_api__canvas_list_targets", arguments={"target_type": "page", "limit": 3}, confirmed=True)
    check("M8 a confirmed extra runs", "error" not in done, f"{seconds:.1f}s: {str(done)[:160]}")
    for name in ("tool_api__canvas_publish_auto_saves", "tool_api__canvas_set_homepage", "tool_api__demo_entity_save"):
        refused = call(DrupalSiteAction(), action=name, arguments={}, confirmed=True)
        check(f"M8 {name.split('__')[-1]} is never available", "not available" in refused.get("error", ""))

    backend.close()
    print(f"\n{sum(_results)}/{len(_results)} passed. Delete the draft {title!r} on the site when convenient.")
    return 0 if all(_results) else 1


if __name__ == "__main__":
    sys.exit(main())
