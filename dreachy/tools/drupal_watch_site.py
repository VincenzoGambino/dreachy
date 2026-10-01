"""External tool: background watcher that reacts to new site content.

Runs its own persistent asyncio task rather than going through
BackgroundToolManager's one-shot start/result model — that model only
supports a single spoken announcement per call, when the tool's coroutine
returns, with no way to push further interim announcements from a
still-running task. So on each detection this plays a notification sound and
a physical reaction directly (both are hardware calls, not routed through
the LLM/speech pipeline, so neither is subject to that limitation) — no
narration needed. The tool call itself only speaks once, to confirm it
started.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from dreachy.backend import Backend, DreachySiteError
from dreachy.common_reactions import REACTIONS
from dreachy.reaction_player import play_reaction
from dreachy.tools._shared import get_client
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies

logger = logging.getLogger(__name__)

_watch_task: asyncio.Task[None] | None = None

# How often a sleeping watcher checks whether the settings page swapped the
# client, so a newly saved site URL isn't stuck behind a long backoff sleep.
_CLIENT_CHECK_SECONDS = 1.0


def _watched(client: Backend) -> tuple:
    """What the watcher's baseline belongs to: the site, and which types count."""
    config = client.config
    return (config.base_url, config.default_locale, tuple(config.enabled_types))


async def _latest_created(client: Backend) -> str | None:
    nodes = await asyncio.to_thread(client.get_recent_nodes, limit=1)
    return nodes[0]["created"] if nodes else None


async def _sleep_unless_client_changes(seconds: float, client: Backend) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while (remaining := deadline - loop.time()) > 0:
        if get_client() is not client:
            return
        await asyncio.sleep(min(remaining, _CLIENT_CHECK_SECONDS))


async def _watch_loop(deps: ToolDependencies) -> None:
    client = get_client()
    consecutive_failures = 0

    try:
        last_seen_created = await _latest_created(client)
        baseline_established = True
    except DreachySiteError as exc:
        logger.warning("drupal_watch_site: couldn't establish a baseline: %s", exc)
        last_seen_created = None
        baseline_established = False

    while True:
        interval = client.config.poll_interval_seconds
        sleep_for = interval * (2**consecutive_failures) if consecutive_failures else interval
        await _sleep_unless_client_changes(min(sleep_for, client.config.watch_max_backoff_seconds), client)

        current = get_client()
        if current is not client:
            # The settings page saved: every save swaps the client. Only a
            # different site (URL or language prefix) or type selection means
            # a new baseline — its newest content isn't news. On the same
            # site, content published since the last poll still is.
            if _watched(current) == _watched(client):
                logger.info("drupal_watch_site: settings changed; still watching %s", current.config.base_url)
            else:
                logger.info("drupal_watch_site: now watching %s, re-baselining", current.config.base_url)
                last_seen_created = None
                baseline_established = False
            client = current
            consecutive_failures = 0

        try:
            newest = await _latest_created(client)
            consecutive_failures = 0
        except DreachySiteError as exc:
            consecutive_failures += 1
            logger.warning("drupal_watch_site: poll failed (%d in a row): %s", consecutive_failures, exc)
            continue

        if newest is None:
            continue

        if not baseline_established:
            # First successful poll after a failed baseline: record state
            # silently rather than treating "whatever's there now" as new —
            # otherwise this would always fire a spurious reaction on recovery.
            last_seen_created = newest
            baseline_established = True
            continue

        if last_seen_created is None or newest > last_seen_created:
            last_seen_created = newest
            logger.info("drupal_watch_site: new content detected (created=%s), reacting", newest)
            try:
                # A direct hardware call, not routed through the LLM/speech
                # pipeline — unlike a spoken announcement, this isn't subject
                # to the one-shot-per-tool-call limitation, so it's safe to
                # fire from inside this persistent loop.
                deps.reachy_mini.media.play_sound("wake_up.wav")
                await play_reaction(REACTIONS["perk_up"], deps)
            except Exception:
                logger.exception("drupal_watch_site: failed to play reaction")


class DrupalWatchSite(Tool):
    """Start or stop the background site-watcher."""

    name = "drupal_watch_site"
    description = (
        "Start or stop watching the site in the background for new content. Once watching, "
        "Dreachy reacts physically the moment something new is published — there's no need "
        "to narrate every detection. Use when asked to start/stop watching, keep an eye on "
        "the site, or pay attention to updates."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "stop"],
                "description": "Whether to start or stop the background watcher.",
            },
        },
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        global _watch_task
        action = kwargs.get("action")
        if action not in ("start", "stop"):
            return {"error": "action must be 'start' or 'stop'"}

        if action == "start":
            if _watch_task is not None and not _watch_task.done():
                return {"status": "already watching"}
            _watch_task = asyncio.create_task(_watch_loop(deps), name="drupal_watch_site")
            return {"status": "watching", "message": "Started watching the site for new content."}

        if _watch_task is None or _watch_task.done():
            return {"status": "not watching"}
        _watch_task.cancel()
        try:
            await _watch_task
        except asyncio.CancelledError:
            pass
        _watch_task = None
        return {"status": "stopped watching"}
