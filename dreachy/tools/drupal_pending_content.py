"""External tool: what's waiting to be published (R3; logged-in sites only)."""

import asyncio
import logging
from typing import Any, Dict

from dreachy.backend import DreachySiteError
from dreachy.tool_queries import drupal_pending_content
from dreachy.tools._shared import get_client
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies

logger = logging.getLogger(__name__)

# Also returned by drupal_create_note. Registered only when the login works
# (main._start_up), but an installer's AUTOLOAD_EXTERNAL_TOOLS would load it
# anyway — so it checks for itself.
NOT_AVAILABLE = {"error": "Editing isn't available on this site."}


class DrupalPendingContent(Tool):
    """Count and name the content waiting to be published."""

    name = "drupal_pending_content"
    description = (
        "List what's waiting to be published on the site — drafts and anything in review — with counts "
        "and the latest titles. Use when asked what's pending, in draft, or waiting for review."
    )
    parameters_schema = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        client = get_client()
        if not client.config.uses_oauth:
            return dict(NOT_AVAILABLE)
        try:
            return await asyncio.to_thread(drupal_pending_content, client)
        except DreachySiteError as e:
            logger.warning("drupal_pending_content: site error: %s", e)
            return {"error": f"I can't reach the site right now: {e}"}
        except Exception as e:
            logger.exception("drupal_pending_content failed")
            return {"error": f"Something went wrong: {type(e).__name__}: {e}"}
