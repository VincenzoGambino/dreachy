"""An in-memory Backend: the five tools and the watcher run against it with
no HTTP at all, which is what proves they depend on the interface and not
on JSON:API."""

from __future__ import annotations

from typing import Any

from dreachy.backend import Backend, DreachySiteError
from dreachy.config import Config
from dreachy.schema import Schema


def fake_node(uuid: str, title: str, type: str, created: str, *, body: str = "", summary: str = "") -> dict[str, Any]:
    return {
        "id": uuid,
        "title": title,
        "type": type,
        "created": created,
        "changed": created,
        "path": f"/{type}/{uuid}",
        "body": body,
        "summary": summary or body[:200],
    }


class FakeBackend(Backend):
    def __init__(self, nodes: list[dict[str, Any]], config: Config | None = None, *, fail: bool = False) -> None:
        super().__init__(config or Config())
        self.nodes = nodes
        self.fail = fail
        self.closed = False
        # Every read's include_unpublished, in call order.
        self.unpublished_requests: list[bool] = []

    def close(self) -> None:
        self.closed = True

    def _check(self) -> None:
        if self.fail:
            raise DreachySiteError("site down")

    def get_schema(self) -> Schema:
        self._check()
        return self.full_schema

    def _recent(self, limit: int) -> list[dict[str, Any]]:
        return sorted(self.nodes, key=lambda n: n["created"], reverse=True)[:limit]

    def get_recent_nodes(self, limit: int | None = None, *, include_unpublished: bool = False) -> list[dict[str, Any]]:
        self._check()
        self.unpublished_requests.append(include_unpublished)
        return self._recent(limit or self.config.whats_new_limit)

    def find_content(
        self, keyword: str, content_type: str | None = None, *, include_unpublished: bool = False
    ) -> list[dict[str, Any]]:
        self._check()
        self.unpublished_requests.append(include_unpublished)
        return [
            n
            for n in self._recent(len(self.nodes) or 1)
            if keyword.lower() in n["title"].lower() and content_type in (None, n["type"])
        ]

    def get_article(self, title_or_path: str, *, include_unpublished: bool = False) -> dict[str, Any] | None:
        self._check()
        self.unpublished_requests.append(include_unpublished)
        return next((n for n in self.nodes if title_or_path in (n["title"], n["path"])), None)
