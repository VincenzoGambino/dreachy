"""Mocked tests for reaction_player.play_reaction. No real robot, no real sleeping."""

from __future__ import annotations

import asyncio

import numpy as np

from dreachy.common_reactions import Reaction, ReactionStep
from dreachy.reaction_player import play_reaction
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


class _FakeReachyMini:
    def __init__(self) -> None:
        self.head_pose = np.eye(4)
        self.antennas = [0.0, 0.0]

    def get_current_head_pose(self):
        return self.head_pose

    def get_current_joint_positions(self):
        return ([0.0] * 7, list(self.antennas))


class _FakeMovementManager:
    def __init__(self) -> None:
        self.queued_moves: list = []
        self.moving_states: list[float] = []

    def queue_move(self, move) -> None:
        self.queued_moves.append(move)

    def set_moving_state(self, duration: float) -> None:
        self.moving_states.append(duration)


async def _no_op_sleep(seconds: float) -> None:
    pass


def test_play_reaction_queues_a_move_per_step() -> None:
    deps = ToolDependencies(reachy_mini=_FakeReachyMini(), movement_manager=_FakeMovementManager())
    reaction = Reaction(
        name="test",
        steps=[
            ReactionStep(duration=0.4, head={"pitch": -8}, antennas=[-0.5, 0.5]),
            ReactionStep(duration=0.6, hold=0.2),
            ReactionStep(duration=0.8, head={}, antennas=[-0.1, 0.1]),
        ],
    )

    asyncio.run(play_reaction(reaction, deps, sleep=_no_op_sleep))

    assert len(deps.movement_manager.queued_moves) == 3
    assert deps.movement_manager.moving_states == [0.4, 0.6, 0.8]


def test_play_reaction_holds_current_pose_when_step_has_no_head_or_antennas() -> None:
    fake_robot = _FakeReachyMini()
    fake_robot.antennas = [0.2, -0.2]
    deps = ToolDependencies(reachy_mini=fake_robot, movement_manager=_FakeMovementManager())
    reaction = Reaction(name="test", steps=[ReactionStep(duration=0.5)])

    asyncio.run(play_reaction(reaction, deps, sleep=_no_op_sleep))

    move = deps.movement_manager.queued_moves[0]
    assert move.target_antennas == (0.2, -0.2)
    np.testing.assert_array_equal(move.target_head_pose, fake_robot.head_pose)


def test_play_reaction_sleeps_total_of_duration_and_hold() -> None:
    sleep_calls: list[float] = []

    async def recording_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    deps = ToolDependencies(reachy_mini=_FakeReachyMini(), movement_manager=_FakeMovementManager())
    reaction = Reaction(name="test", steps=[ReactionStep(duration=0.6, hold=0.2)])

    asyncio.run(play_reaction(reaction, deps, sleep=recording_sleep))

    assert sleep_calls == [0.8]
