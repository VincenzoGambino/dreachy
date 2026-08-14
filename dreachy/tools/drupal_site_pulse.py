"""External tool: site activity snapshot (the persona's "how am I feeling" data source)."""

import asyncio
import logging
from typing import Any, Dict

from dreachy.client import DreachySiteError
from dreachy.tool_queries import drupal_site_pulse
from dreachy.tools._shared import get_client
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies

logger = logging.getLogger(__name__)


class DrupalSitePulse(Tool):
    """Get a snapshot of how much has been happening on the site."""

    name = "drupal_site_pulse"
    description = (
        "Get a snapshot of site activity: how much content exists and how recently it was "
        "last updated. Use when asked how you're feeling, how things are going, or for a "
        "general status/mood check."
    )
    parameters_schema = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        try:
            return await asyncio.to_thread(drupal_site_pulse, get_client())
        except DreachySiteError as e:
            logger.warning("drupal_site_pulse: site unreachable: %s", e)
            return {"error": f"I can't reach the site right now: {e}"}
        except Exception as e:
            logger.exception("drupal_site_pulse failed")
            return {"error": f"Something went wrong: {type(e).__name__}: {e}"}
