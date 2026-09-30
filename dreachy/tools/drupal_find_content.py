"""External tool: keyword search across site content."""

import asyncio
import logging
from typing import Any, Dict

from dreachy.client import DreachySiteError
from dreachy.tool_queries import drupal_find_content
from dreachy.tools._shared import get_client, or_list, type_choices
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies

logger = logging.getLogger(__name__)


class DrupalFindContent(Tool):
    """Search the site's content by keyword."""

    name = "drupal_find_content"
    # Properties, not class attributes: the conversation app builds tool
    # specs once at start, after main.py has discovered the site's types, so
    # these name the site's own. A type enabled later reaches the spec on the
    # next start; one disabled later makes DrupalClient.find_content widen
    # the search instead of failing.
    @property
    def description(self) -> str:
        return (
            "Search the site's content by keyword, optionally filtered to one content type "
            f"({or_list(type_choices())}). Use when asked to find, search, or look up something "
            "on the site."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "Keyword to search for in titles."},
                "content_type": {
                    "type": "string",
                    "enum": type_choices(),
                    "description": "Optional: restrict the search to one content type.",
                },
            },
            "required": ["keyword"],
        }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        keyword = kwargs.get("keyword")
        if not isinstance(keyword, str) or not keyword:
            return {"error": "keyword is required"}
        try:
            return await asyncio.to_thread(
                drupal_find_content,
                get_client(),
                keyword=keyword,
                content_type=kwargs.get("content_type"),
            )
        except DreachySiteError as e:
            logger.warning("drupal_find_content: site unreachable: %s", e)
            return {"error": f"I can't reach the site right now: {e}"}
        except Exception as e:
            logger.exception("drupal_find_content failed")
            return {"error": f"Something went wrong: {type(e).__name__}: {e}"}
