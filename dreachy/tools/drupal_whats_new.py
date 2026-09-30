"""External tool: latest site content, newest first."""

import asyncio
import logging
from typing import Any, Dict

from dreachy.backend import DreachySiteError
from dreachy.tool_queries import drupal_whats_new
from dreachy.tools._shared import get_client
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies

logger = logging.getLogger(__name__)


class DrupalWhatsNew(Tool):
    """Get the latest content published on the site."""

    name = "drupal_whats_new"
    description = (
        "Get the latest content published on the site, newest first. Use when asked what's "
        "new, what's recent, or what's been posted lately."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of items to return.",
            },
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        try:
            return await asyncio.to_thread(drupal_whats_new, get_client(), limit=kwargs.get("limit"))
        except DreachySiteError as e:
            logger.warning("drupal_whats_new: site unreachable: %s", e)
            return {"error": f"I can't reach the site right now: {e}"}
        except Exception as e:
            logger.exception("drupal_whats_new failed")
            return {"error": f"Something went wrong: {type(e).__name__}: {e}"}
