"""Query-shaping logic behind the Q&A and editorial tools: drupal_whats_new,
drupal_find_content, drupal_site_pulse, drupal_read_article, and (R3)
drupal_pending_content and drupal_create_note.

Plain functions, not Tool subclasses. Each function takes an already-
configured Backend and raises DreachySiteError on real site failures
rather than catching it; only the Tool.__call__ boundary must never raise,
so the catching happens there, not here.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .backend import Backend


def _humanize_age(timestamp: str) -> str:
    """Convert an ISO 8601 timestamp into a short, speakable relative age."""
    created = datetime.fromisoformat(timestamp)
    days = (datetime.now(timezone.utc) - created).days

    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"

    weeks = days // 7
    if weeks < 5:
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"

    months = days // 30
    return f"{months} month{'s' if months != 1 else ''} ago"


def drupal_whats_new(client: Backend, *, limit: int | None = None) -> dict[str, Any]:
    nodes = client.get_recent_nodes(limit=limit)
    return {
        "items": [
            {"title": node["title"], "type": node["type"], "age": _humanize_age(node["created"])}
            for node in nodes
        ]
    }


def drupal_find_content(
    client: Backend, *, keyword: str, content_type: str | None = None
) -> dict[str, Any]:
    matches = client.find_content(keyword, content_type=content_type)
    return {
        "matches": [
            {"title": node["title"], "type": node["type"], "teaser": node["summary"]}
            for node in matches
        ]
    }


def drupal_site_pulse(client: Backend) -> dict[str, Any]:
    pulse = client.get_site_pulse()
    latest = pulse["latest_node_created"]
    return {
        "node_count": pulse["node_count"],
        "latest_activity_age": _humanize_age(latest) if latest else None,
    }


def drupal_read_article(client: Backend, *, title_or_path: str) -> dict[str, Any] | None:
    article = client.get_article(title_or_path)
    if article is None:
        return None
    return {"title": article["title"], "text": article["body"]}


def drupal_pending_content(client: Backend) -> dict[str, Any]:
    nodes = client.get_pending_nodes()
    states = [node["moderation_state"] or "unpublished" for node in nodes]
    return {
        "count": len(nodes),
        "by_state": dict(Counter(states)),
        "latest": [
            {"title": node["title"], "type": node["type"], "state": state, "age": _humanize_age(node["changed"])}
            for node, state in list(zip(nodes, states))[:3]
        ],
    }


def drupal_create_note(client: Backend, *, title: str, body: str) -> dict[str, Any]:
    # The installer's note type, else the first enabled type (spec R3.3).
    content_type = client.config.note_type or next(iter(client.schema))
    node = client.create_draft(content_type, title, body)
    return {"saved": "draft", "title": node["title"], "type": node["type"]}
