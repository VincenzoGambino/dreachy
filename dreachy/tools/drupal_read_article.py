"""External tool: fetch full article/page/recipe text for reading aloud."""

import asyncio
import logging
from typing import Any, Dict

from dreachy.backend import DreachySiteError
from dreachy.tool_queries import drupal_read_article
from dreachy.tools._shared import get_client, or_list, type_choices
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies

logger = logging.getLogger(__name__)


class DrupalReadArticle(Tool):
    """Fetch the full text of one piece of content, for reading aloud."""

    name = "drupal_read_article"
    # A property for the same reason as DrupalFindContent's: it names the
    # site's own types, discovered before the specs are built.
    @property
    def description(self) -> str:
        return (
            f"Fetch the full text of one {or_list(type_choices())} by title or URL path, for "
            "reading aloud. Use when asked to read something, or to read a piece of content out loud."
        )

    parameters_schema = {
        "type": "object",
        "properties": {
            "title_or_path": {
                "type": "string",
                "description": "The title, or URL path, of the content to read.",
            },
        },
        "required": ["title_or_path"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        title_or_path = kwargs.get("title_or_path")
        if not isinstance(title_or_path, str) or not title_or_path:
            return {"error": "title_or_path is required"}
        try:
            result = await asyncio.to_thread(drupal_read_article, get_client(), title_or_path=title_or_path)
        except DreachySiteError as e:
            logger.warning("drupal_read_article: site unreachable: %s", e)
            return {"error": f"I can't reach the site right now: {e}"}
        except Exception as e:
            logger.exception("drupal_read_article failed")
            return {"error": f"Something went wrong: {type(e).__name__}: {e}"}

        if result is None:
            return {"error": f"I couldn't find anything matching '{title_or_path}'."}
        return result
