"""Executes a Reaction's steps against the real robot.

Bridges common_reactions.py's app-agnostic reaction data to the specific
robot-control mechanism reachy_mini_conversation_app exposes to tools — the
same GotoQueueMove/MovementManager pattern used by its own move_head tool.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from reachy_mini.utils import create_head_pose
from reachy_mini_conversation_app.dance_emotion_moves import GotoQueueMove
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies

from dreachy.common_reactions import Reaction

logger = logging.getLogger(__name__)

SleepFn = Callable[[float], Awaitable[None]]


async def play_reaction(
    reaction: Reaction, deps: ToolDependencies, *, sleep: SleepFn = asyncio.sleep
) -> None:
    """Play a Reaction's steps in sequence on the real robot.

    ``sleep`` is a test seam (same DI pattern as JsonApiBackend's http_client) —
    tests inject a no-op so the sequencing logic runs instantly instead of
    waiting out each step's real duration.
    """
    for step in reaction.steps:
        current_head_pose = deps.reachy_mini.get_current_head_pose()
        _, current_antennas = deps.reachy_mini.get_current_joint_positions()

        target_head_pose = create_head_pose(**step.head) if step.head is not None else current_head_pose
        target_antennas = tuple(step.antennas) if step.antennas is not None else tuple(current_antennas)

        goto_move = GotoQueueMove(
            target_head_pose=target_head_pose,
            start_head_pose=current_head_pose,
            target_antennas=target_antennas,
            start_antennas=(current_antennas[0], current_antennas[1]),
            duration=step.duration,
        )
        deps.movement_manager.queue_move(goto_move)
        deps.movement_manager.set_moving_state(step.duration)
        await sleep(step.duration + step.hold)
