"""Reaction vocabulary: named motion sequences defined as data.

Each Reaction is a list of ReactionStep dicts, each mapping directly to one
goto_target call. Execution (translating steps into robot commands) is the
caller's job, not this module's — this file is pure data (see
dreachy/reaction_player.py for Dreachy's own execution layer).

Coordinate system (create_head_pose):
  x = forward (+) / backward (−)
  y = robot-right (+) / robot-left (−)
  z = up (+) / down (−)
  mm=True interprets x/y/z in millimetres.
  degrees=True (default) interprets roll/pitch/yaw in degrees.

Antenna ordering: [right_rad, left_rad].
Antenna limits: no explicit software clamp in SDK; MJCF has no range for antenna
  joints. Largest value in examples is 0.785 rad (≈ 45°); stay ≤ 1.05 rad (60°).

Duration floor: 0.3 s.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReactionStep:
    duration: float
    head: Optional[dict] = None      # kwargs for create_head_pose
    antennas: Optional[list[float]] = None  # [right_rad, left_rad]
    hold: float = 0.0                # additional hold after the goto completes


@dataclass
class Reaction:
    name: str
    steps: list[ReactionStep] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Rest / neutral positions (shared constants used across reactions)
# ---------------------------------------------------------------------------

_ANTENNAS_REST = [-0.1745, 0.1745]   # ≈ ±10°
_ANTENNAS_FLARE = [-0.524, 0.524]    # ±30°

_HEAD_NEUTRAL = {}   # create_head_pose() with no args → identity


# ---------------------------------------------------------------------------
# Reaction definitions
# ---------------------------------------------------------------------------

REACTIONS: dict[str, Reaction] = {
    # ------------------------------------------------------------------
    # perk_up — new content detected
    # Antennas flare quickly, small head lift, ease back.
    # ------------------------------------------------------------------
    "perk_up": Reaction(
        name="perk_up",
        steps=[
            ReactionStep(
                duration=0.4,
                head={"pitch": -8},       # slight upward tilt
                antennas=_ANTENNAS_FLARE,
            ),
            ReactionStep(duration=0.6, hold=0.2),  # hold
            ReactionStep(
                duration=0.8,
                head=_HEAD_NEUTRAL,
                antennas=_ANTENNAS_REST,
            ),
        ],
    ),
}
