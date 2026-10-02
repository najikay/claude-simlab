"""Medium presets and vehicle models for the swarm lab (R-8.33c, R-8.33d).

**Medium** (``--medium air|water|fog``): a named set of changes to the world that the behaviours
never see directly. ``air`` is the default and changes nothing. ``water`` adds drag on every
agent's velocity, a constant current that carries everyone, and acoustic-style communication
(shorter range, seconds of latency, more loss). ``fog`` is a *sensing* preset: the obstacle sense
range shrinks, range/bearing measurements between agents fail more often and are noisier. Each
preset states what it is not: none of this is fluid dynamics or light transport, only the
comparable, describable conditions a swarm question needs (see docs/research/14, layer B).

**Vehicle** (``--vehicle point|unicycle|fixed-wing|quadrotor-lite``): how an agent turns the
velocity a behaviour asks for into the velocity it actually gets. ``point`` (default) is the
point mass with a speed and an acceleration limit. ``unicycle`` cannot move sideways: it turns
its heading at most ``max_turn`` rad/s and moves along it at speed >= 0. ``fixed-wing`` is a
unicycle that must keep at least ``min_speed`` (it cannot hover or stop) with a wider turn.
``quadrotor-lite`` is a point mass with half the acceleration and a little drag (a hover-capable
vehicle that is slower to respond). Simple on purpose; Gazebo is the place for real dynamics.
"""

import math
from dataclasses import dataclass

import numpy as np

MEDIA = ["air", "water", "fog"]
VEHICLES = ["point", "unicycle", "fixed-wing", "quadrotor-lite"]


@dataclass
class Medium:
    """What a medium changes; multipliers apply to the experiment's own values."""

    name: str = "air"
    drag: float = 0.0  # fraction of velocity lost per second
    current: tuple[float, float] = (0.0, 0.0)  # m/s carried along
    comm_range_scale: float = 1.0
    latency_add: int = 0  # ticks added to every message
    loss_add: float = 0.0  # added to the message-loss probability
    sense_scale: float = 1.0  # obstacle sense range multiplier
    rb_dropout: float = 0.0  # probability a range/bearing measurement is missed
    range_noise_scale: float = 1.0
    note: str = "changes nothing"

    def to_dict(self) -> dict:
        """For the manifest and the metrics file."""
        return {
            "name": self.name,
            "drag": self.drag,
            "current": list(self.current),
            "comm_range_scale": self.comm_range_scale,
            "latency_add": self.latency_add,
            "loss_add": self.loss_add,
            "sense_scale": self.sense_scale,
            "rb_dropout": self.rb_dropout,
            "range_noise_scale": self.range_noise_scale,
            "note": self.note,
        }


def medium_named(name: str, current: tuple[float, float] | None = None) -> Medium:
    """Return a preset by name; ``current`` overrides the water preset's default current."""
    if name in ("air", "", None):
        return Medium()
    if name == "water":
        cur = current if current is not None else (0.3, 0.0)
        return Medium(
            name="water",
            drag=0.4,
            current=cur,
            comm_range_scale=0.5,
            latency_add=5,
            loss_add=0.2,
            note=(
                f"drag 40 %/s, a current of ({cur[0]:g}, {cur[1]:g}) m/s, acoustic links: half the "
                "range, +5 ticks latency, +20 % loss; not hydrodynamics, no depth, no buoyancy"
            ),
        )
    if name == "fog":
        return Medium(
            name="fog",
            sense_scale=0.4,
            rb_dropout=0.5,
            range_noise_scale=2.0,
            note=(
                "sensing only: obstacles sensed at 40 % of the range, half the range/bearing "
                "measurements missed and the rest twice as noisy; radio unchanged; not light "
                "transport"
            ),
        )
    raise ValueError(f"unknown medium {name!r}; one of {MEDIA}")


def apply_drag(v: np.ndarray, m: Medium, dt: float, min_speed: float = 0.0) -> np.ndarray:
    """Return the vehicle's own velocity after one tick of drag (the current is added later).

    A vehicle with a minimum speed (fixed-wing) holds it against the drag: it keeps flying.
    """
    out = v * max(0.0, 1.0 - m.drag * dt)
    if min_speed > 0:
        sp = np.linalg.norm(out, axis=-1, keepdims=True)
        slow = (sp < min_speed) & (sp > 1e-9)
        out = np.where(slow, out / np.maximum(sp, 1e-9) * min_speed, out)
    return out


@dataclass
class VehicleLimits:
    """Per-model limits derived from the experiment's max_speed / max_accel."""

    model: str = "point"
    max_speed: float = 2.0
    max_accel: float = 4.0
    max_turn: float = 1.5  # rad/s (unicycle, fixed-wing)
    min_speed: float = 0.0  # fixed-wing keeps moving


def limits_for(model: str, max_speed: float, max_accel: float) -> VehicleLimits:
    """Return the limits a model gets from the experiment's speed and acceleration."""
    if model == "point":
        return VehicleLimits("point", max_speed, max_accel)
    if model == "unicycle":
        return VehicleLimits("unicycle", max_speed, max_accel, max_turn=2.0)
    if model == "fixed-wing":
        return VehicleLimits("fixed-wing", max_speed, max_accel, max_turn=0.9, min_speed=0.5 * max_speed)
    if model == "quadrotor-lite":
        return VehicleLimits("quadrotor-lite", max_speed, max_accel * 0.5)
    raise ValueError(f"unknown vehicle {model!r}; one of {VEHICLES}")


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def track(
    v_des: np.ndarray, vel: np.ndarray, heading: float, lim: VehicleLimits, dt: float
) -> tuple[np.ndarray, float]:
    """One tick of the vehicle following a desired velocity; returns (velocity, heading)."""
    if lim.model in ("point", "quadrotor-lite"):
        dv = v_des - vel
        m = float(np.linalg.norm(dv))
        if m > lim.max_accel * dt:
            dv = dv / m * lim.max_accel * dt
        v = vel + dv
        if lim.model == "quadrotor-lite":
            v = v * max(0.0, 1.0 - 0.1 * dt)
        sp = float(np.linalg.norm(v))
        if sp > lim.max_speed:
            v = v / sp * lim.max_speed
        h = math.atan2(v[1], v[0]) if sp > 1e-6 else heading
        return v, h
    # non-holonomic: turn towards the demand at a bounded rate, then move along the heading
    want_sp = float(np.linalg.norm(v_des))
    if want_sp > 1e-6:
        want_h = math.atan2(v_des[1], v_des[0])
        err = _wrap(want_h - heading)
        step = max(-lim.max_turn * dt, min(lim.max_turn * dt, err))
        heading = _wrap(heading + step)
        # off-axis demand costs speed (a sharp turn slows a real vehicle), never below the minimum
        cos_err = max(0.0, math.cos(err))
        target_sp = max(lim.min_speed, min(lim.max_speed, want_sp * (0.3 + 0.7 * cos_err)))
    else:
        target_sp = lim.min_speed
    cur_sp = float(vel @ np.array([math.cos(heading), math.sin(heading)]))
    d_sp = max(-lim.max_accel * dt, min(lim.max_accel * dt, target_sp - cur_sp))
    sp = min(lim.max_speed, cur_sp + d_sp)
    if cur_sp >= lim.min_speed:  # never decelerate below the minimum; from below, climb at max_accel
        sp = max(lim.min_speed, sp)
    return np.array([math.cos(heading), math.sin(heading)]) * sp, heading
