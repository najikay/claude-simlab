"""Swarm / multi-agent simulator : communication, decisions, planning under uncertainty.

Pure NumPy, 2-D, discrete ticks. Built for the master's questions rather than for pretty physics:

- **agents** with position and velocity (max speed, max acceleration), noisy self-localisation
  (Gaussian) filtered into a *belief* (exponential filter; swap in a Kalman filter later);
- a **communication graph**: an edge between two agents when within ``comm_range``; every message
  is lost with probability ``msg_loss`` and delayed by ``msg_latency`` ticks; neighbour beliefs
  are what an agent knows, not the truth;
- **behaviours** (the planning side): ``flock`` (Reynolds: separation, alignment, cohesion),
  ``formation`` (consensus to offsets around the centroid), ``rendezvous`` (average consensus),
  ``coverage`` (each agent moves to the least-covered grid cell it knows about), ``goto``
  (assigned target; greedy task allocation);
- a **decision hook** per agent every ``decision_every`` ticks: ``policy(view) -> behaviour``.
  ``rules`` is the baseline (separation first, then the mission, rendezvous when isolated); ``random``
is a control.
- **beliefs**: ``exp`` (exponential filter), ``kf`` (constant-velocity Kalman on position fixes,
  optionally fusing neighbours' fixes of us) or ``ekf`` (unicycle EKF on (x, y, heading) predicted
  from noisy odometry, corrected by an own position fix every ``fix_every`` ticks and by **range and
  bearing to neighbours** whose broadcast beliefs act as landmarks: cooperative localisation);
- **obstacles** (``--layout pillars|rooms|corridor`` or ``--obstacles`` JSON): agents sense them
  within ``sense_range`` and are pushed away, cannot enter them (a *hit* projects them out), and
  with ``--comms los`` a link needs line of sight (walls block radio);
- **medium** (``--medium air|water|fog``, ``--current x,y``): water adds drag, a current and
  acoustic-style links; fog shrinks sensing and drops range/bearing measurements (dynamics.py);
- **vehicle** (``--vehicle point|unicycle|fixed-wing|quadrotor-lite``): how the velocity a
  behaviour asks for becomes the one the agent gets (turn-rate limits, minimum speed, drag);
- **metrics**: formation error, consensus error, coverage fraction, mean neighbour count,
  messages sent / lost, collisions (distance < ``collision_r``), decisions by source, ticks, and
  the communication graph over time (edges, connected components, algebraic connectivity λ2).

Outputs (into ``--out``): ``agents.jsonl`` (one line per tick per agent: t, id, x, y, vx, vy,
behaviour, n_neighbours), ``comms.jsonl`` (per tick: edges, messages sent / lost), ``metrics.json``
(``headline`` plus the full series), ``decisions.jsonl`` (per decision: agent, source, choice,
confidence). No ROS, no Gazebo; the same contract runs through the Sim Lab.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import world
import tasks as task_alloc
from tasks import POLICIES as ALLOC_POLICIES
from tasks import TaskBoard
from dynamics import (
    MEDIA,
    VEHICLES,
    apply_drag,
    limits_for,
    medium_named,
    track,
)
from world import LAYOUTS, Obstacles


def _layout_arg(v: str) -> str:
    """A named layout, or plan:<name> for a floor plan file; checked here so a typo fails fast."""
    if v in LAYOUTS or v.startswith("plan:"):
        return v
    raise argparse.ArgumentTypeError(f"{v!r}: one of {LAYOUTS} or plan:<name>")


BEHAVIOURS = ["flock", "formation", "rendezvous", "coverage", "goto", "tasks"]  # tasks: goto the allocated task, coverage while none is known

# Extension points for a project that embeds the simulator (load this file as a module, register,
# then call ``main()``); nothing is registered by default. A policy is called once per decision tick
# with every agent's view and the parsed arguments, and returns one (behaviour, confidence, source)
# per view; if it raises, that tick falls back to the rules. An argument hook adds its own flags.
EXTRA_POLICIES: dict = {}  # name -> callable(views: list[dict], args: argparse.Namespace) -> list[tuple]
EXTRA_ARGUMENTS: list = []  # callables(parser: argparse.ArgumentParser) -> None


class KalmanBelief:
    """Constant-velocity Kalman filter per agent over (x, y, vx, vy) from noisy position fixes.

    ``update`` fuses the agent's own fix; ``fuse_neighbour`` fuses a neighbour's estimate of us
    (sensor fusion across the swarm) with its own covariance. Same interface as the exponential
    filter: ``predict(dt)``, ``update(z, r)``, ``pos``.
    """

    def __init__(self, x0: np.ndarray, q: float, r: float) -> None:
        self.x = np.array([x0[0], x0[1], 0.0, 0.0])
        self.P = np.eye(4) * 1.0
        self.q, self.r = q, r
        self.H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=float)

    def predict(self, dt: float) -> None:
        """Propagate with constant velocity."""
        F = np.array([[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=float)
        Q = np.diag([0.25 * dt**4, 0.25 * dt**4, dt**2, dt**2]) * self.q
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q

    def update(self, z: np.ndarray, r: float | None = None) -> None:
        """Fuse a position measurement with noise variance ``r``."""
        R = np.eye(2) * (r if r is not None else self.r) ** 2
        S = self.H @ self.P @ self.H.T + R
        K = self.P @ self.H.T @ np.linalg.inv(S)
        self.x = self.x + K @ (z - self.H @ self.x)
        self.P = (np.eye(4) - K @ self.H) @ self.P

    @property
    def pos(self) -> np.ndarray:
        """Current position estimate."""
        return self.x[:2]

    @property
    def pos_sigma(self) -> float:
        """One-sigma position uncertainty (m), for information-aware behaviours later."""
        return float(np.sqrt(max(self.P[0, 0], self.P[1, 1])))


class UnicycleEKF:
    """EKF over (x, y, heading) with a unicycle motion model and range/bearing measurements to neighbours.

    Prediction uses noisy odometry (speed ``v``, turn rate ``w``): x += v cos θ dt, y += v sin θ dt,
    θ += w dt. Corrections: ``update_fix`` (own position fix, linear) and ``update_range_bearing``
    (range ``r`` and bearing ``b`` to a landmark at ``lm``: h = [sqrt(q), atan2(dy, dx) - θ], the
    classic EKF-localisation Jacobian; the landmark is a neighbour's *broadcast belief*, so its own
    error enters the measurement noise). Same ``pos`` / ``pos_sigma`` interface as ``KalmanBelief``.
    """

    def __init__(self, x0: np.ndarray, heading: float, odom_noise: float, fix_noise: float) -> None:
        self.x = np.array([x0[0], x0[1], heading], dtype=float)
        self.P = np.diag([1.0, 1.0, 0.1])
        self.odom_noise, self.fix_noise = odom_noise, fix_noise

    @staticmethod
    def _wrap(a: float) -> float:
        return (a + math.pi) % (2 * math.pi) - math.pi

    def predict(self, v: float, w: float, dt: float) -> None:
        """Propagate with the unicycle model; process noise grows with the motion."""
        th = self.x[2]
        self.x = self.x + np.array([v * math.cos(th) * dt, v * math.sin(th) * dt, w * dt])
        self.x[2] = self._wrap(self.x[2])
        F = np.array([[1, 0, -v * math.sin(th) * dt], [0, 1, v * math.cos(th) * dt], [0, 0, 1]])
        # matches the noise the simulator injects: speed noise relative to the speed, turn-rate noise additive
        sv = self.odom_noise * abs(v) * dt + 0.005
        st = self.odom_noise * dt + 0.002
        Q = np.diag([sv**2, sv**2, st**2])
        self.P = F @ self.P @ F.T + Q

    def _correct(self, y: np.ndarray, H: np.ndarray, R: np.ndarray, gate: float | None = None) -> bool:
        S = H @ self.P @ H.T + R
        S_inv = np.linalg.inv(S)
        if (
            gate is not None and float(y @ S_inv @ y) > gate
        ):  # Mahalanobis gate: an innovation this unlikely is an outlier
            return False
        K = self.P @ H.T @ S_inv
        self.x = self.x + K @ y
        self.x[2] = self._wrap(self.x[2])
        self.P = (np.eye(3) - K @ H) @ self.P
        return True

    def update_fix(self, z: np.ndarray, r: float | None = None) -> None:
        """Own position fix (GPS-like), linear in the state."""
        H = np.array([[1, 0, 0], [0, 1, 0]], dtype=float)
        sig = r if r is not None else self.fix_noise
        self._correct(z - self.x[:2], H, np.eye(2) * sig**2)

    def update_range_bearing(
        self, lm: np.ndarray, rng: float, bearing: float, sig_r: float, sig_b: float
    ) -> bool:
        """Range and bearing (rad, relative to the heading) to a landmark at ``lm``; False when gated out."""
        dx, dy = float(lm[0] - self.x[0]), float(lm[1] - self.x[1])
        q = dx * dx + dy * dy
        if q < 1e-6:
            return False
        sq = math.sqrt(q)
        h = np.array([sq, self._wrap(math.atan2(dy, dx) - self.x[2])])
        y = np.array([rng - h[0], self._wrap(bearing - h[1])])
        H = np.array([[-dx / sq, -dy / sq, 0.0], [dy / q, -dx / q, -1.0]])
        return self._correct(y, H, np.diag([sig_r**2, sig_b**2]), gate=9.21)  # chi-square, 2 dof, 99 %

    def update_range_bearing_ci(
        self, lm: np.ndarray, rng: float, bearing: float, sig_r: float, sig_b: float, sig_lm: float
    ) -> bool:
        """The same measurement fused by covariance intersection (consistent under unknown correlation).

        The neighbour's broadcast position was itself built from messages we sent, so the naive EKF
        update (which assumes independence) grows overconfident and the whole swarm drifts together.
        CI turns range + bearing into a pseudo position fix ``me = lm - r [cos(θ+b), sin(θ+b)]`` with
        its covariance (range, bearing, our heading, the landmark's own sigma) and fuses the position
        block with ``P⁻¹ = ω P⁻¹ + (1-ω) R⁻¹``, ω picked to minimise the trace (Julier & Uhlmann).
        The heading, which no neighbour broadcasts and so is not part of the loop, is then corrected
        by a scalar Kalman update from the bearing residual.
        """
        ang = self.x[2] + bearing
        c, sn = math.cos(ang), math.sin(ang)
        z = np.array([lm[0] - rng * c, lm[1] - rng * sn])
        J = np.array([[-c, rng * sn], [-sn, -rng * c]])  # d me / d(r, b)
        R = J @ np.diag([sig_r**2, sig_b**2 + self.P[2, 2]]) @ J.T + np.eye(2) * sig_lm**2
        P_pp = self.P[:2, :2]
        y = z - self.x[:2]
        R_inv = np.linalg.inv(R)
        if float(y @ np.linalg.inv(P_pp + R) @ y) > 9.21:
            return False
        P_inv = np.linalg.inv(P_pp)
        best: tuple[float, np.ndarray, np.ndarray] | None = None
        for w in np.linspace(0.05, 1.0, 20):
            P_new = np.linalg.inv(w * P_inv + (1 - w) * R_inv)
            tr = float(np.trace(P_new))
            if best is None or tr < best[0]:
                best = (tr, P_new, P_new @ (w * P_inv @ self.x[:2] + (1 - w) * R_inv @ z))
        assert best is not None
        self.x[:2] = best[2]
        self.P[:2, :2] = best[1]
        self.P[:2, 2] = self.P[2, :2] = (
            0.0  # cross terms dropped (conservative: no position↔heading information kept)
        )
        # heading: the bearing residual at the corrected position implies a heading with variance var_b;
        # one-dimensional covariance intersection keeps whichever is more certain (never the naive product,
        # which would collapse P_θθ under the same correlated landmark errors)
        dx, dy = float(lm[0] - self.x[0]), float(lm[1] - self.x[1])
        q = dx * dx + dy * dy
        if q > 1e-6:
            implied = self._wrap(math.atan2(dy, dx) - bearing)
            var_b = sig_b**2 + (sig_lm**2 + float(np.trace(self.P[:2, :2])) / 2) / q
            if (
                var_b < self.P[2, 2]
                and (self._wrap(implied - self.x[2])) ** 2 / (var_b + self.P[2, 2]) < 6.63
            ):
                self.x[2], self.P[2, 2] = implied, var_b
        return True

    @property
    def pos(self) -> np.ndarray:
        """Current position estimate."""
        return self.x[:2]

    @property
    def heading(self) -> float:
        """Current heading estimate (rad)."""
        return float(self.x[2])

    @property
    def pos_sigma(self) -> float:
        """One-sigma position uncertainty (m)."""
        return float(np.sqrt(max(self.P[0, 0], self.P[1, 1])))


def graph_connectivity(n: int, edges: list[tuple[int, int]]) -> tuple[int, float]:
    """Connected components and algebraic connectivity (second-smallest Laplacian eigenvalue, λ2).

    λ2 > 0 iff the graph is connected; the larger it is, the faster consensus converges
    (Olfati-Saber & Murray). Computed on the unweighted graph of this tick.
    """
    if n <= 1:
        return n, 0.0
    L = np.zeros((n, n))
    for i, j in edges:
        L[i, i] += 1
        L[j, j] += 1
        L[i, j] -= 1
        L[j, i] -= 1
    ev = np.linalg.eigvalsh(L)
    components = int((ev < 1e-8).sum())
    return components, (0.0 if components > 1 else float(max(0.0, ev[1])))


def local_lambda2(me: np.ndarray, others: list[np.ndarray], comm_range: float) -> float:
    """λ2 of the weighted graph an agent can see: itself and the neighbours it holds beliefs about.

    Edge weight falls linearly from 1 at zero distance to 0 at ``comm_range`` (the usual proxy in
    connectivity control, after Zavlanos and Pappas), so the estimate changes smoothly as agents move
    instead of stepping between 0 and 2 the way the unweighted λ2 does at low degree. This is the
    agent's own estimate (what it knows, one tick old), not the swarm's true λ2: the connectivity-aware
    behaviour plans on it, and the metrics report the true one (unweighted, from the real links).
    """
    pts = [me, *others]
    n = len(pts)
    if n <= 1:
        return 0.0
    L = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            w = max(0.0, 1.0 - float(np.linalg.norm(pts[i] - pts[j])) / comm_range)
            if w > 0:
                L[i, i] += w
                L[j, j] += w
                L[i, j] -= w
                L[j, i] -= w
    ev = np.linalg.eigvalsh(L)
    return float(max(0.0, ev[1]))


class Comms:
    """Range graph with loss and latency; delivers neighbour beliefs a few ticks late."""

    def __init__(
        self,
        n: int,
        comm_range: float,
        msg_loss: float,
        msg_latency: int,
        rnd: random.Random,
        los: Obstacles | None = None,
        budget: int = 0,
    ) -> None:
        self.n, self.range, self.loss, self.latency, self.rnd = n, comm_range, msg_loss, msg_latency, rnd
        self.budget = budget  # messages an agent may send per tick; 0 = one to every neighbour
        self.los = los  # when set, a link also needs line of sight past these obstacles
        self.blocked = 0  # pairs in range but without line of sight, this tick
        self.queue: list[tuple[int, int, int, np.ndarray]] = []  # (deliver_at, src, dst, belief)
        self.known: list[dict[int, tuple[int, np.ndarray]]] = [
            {} for _ in range(n)
        ]  # dst -> {src: (tick, belief)}
        self.sent = 0
        self.lost = 0
        self.edges: list[tuple[int, int]] = []
        self.links_used: list[tuple[int, int]] = []

    def step(self, tick: int, pos: np.ndarray, beliefs: np.ndarray) -> None:
        """Rebuild the graph from true positions, send beliefs along edges, deliver what is due."""
        d = np.linalg.norm(pos[:, None, :] - pos[None, :, :], axis=2)
        adj = (d <= self.range) & ~np.eye(self.n, dtype=bool)
        pairs = [(i, j) for i in range(self.n) for j in range(i + 1, self.n) if adj[i, j]]
        if self.los is not None and self.los:
            self.edges = [(i, j) for i, j in pairs if not self.los.blocks(pos[i], pos[j])]
            self.blocked = len(pairs) - len(self.edges)
        else:
            self.edges, self.blocked = pairs, 0
        self.links_used = self.pick_links(self.edges)
        for src, dst in self.links_used:
            self.sent += 1
            if self.rnd.random() < self.loss:
                self.lost += 1
                continue
            self.queue.append((tick + self.latency, src, dst, beliefs[src].copy()))
        due = [m for m in self.queue if m[0] <= tick]
        self.queue = [m for m in self.queue if m[0] > tick]
        for _, src, dst, belief in due:
            self.known[dst][src] = (tick, belief)

    def pick_links(self, edges: list[tuple[int, int]]) -> list[tuple[int, int]]:
        """The directed sends of this tick: every link both ways, or at most ``budget`` per agent, chosen at random."""
        if self.budget <= 0:
            return [(s, d) for i, j in edges for s, d in ((i, j), (j, i))]
        out_of: dict[int, list[int]] = {}
        for i, j in edges:
            out_of.setdefault(i, []).append(j)
            out_of.setdefault(j, []).append(i)
        sends: list[tuple[int, int]] = []
        for src, dsts in out_of.items():
            chosen = dsts if len(dsts) <= self.budget else self.rnd.sample(dsts, self.budget)
            sends.extend((src, d) for d in chosen)
        return sends

    def neighbours(self, i: int, tick: int, max_age: int) -> dict[int, np.ndarray]:
        """Beliefs an agent holds about others, dropping stale ones."""
        return {j: b for j, (t, b) in self.known[i].items() if tick - t <= max_age}


def behaviour_velocity(
    kind: str,
    i: int,
    belief: np.ndarray,
    vel: np.ndarray,
    nbrs: dict[int, np.ndarray],
    formation_offsets: np.ndarray,
    coverage_grid: np.ndarray,
    target: np.ndarray | None,
    arena: float,
    max_speed: float,
) -> np.ndarray:
    """Desired velocity for one agent under one behaviour, from what it knows."""
    p = belief
    if kind == "flock":
        if not nbrs:
            return vel * 0.9
        q = np.array(list(nbrs.values()))
        diff = p - q
        dist = np.linalg.norm(diff, axis=1) + 1e-6
        sep = (diff / dist[:, None] ** 2).sum(axis=0)
        coh = q.mean(axis=0) - p
        v = 1.5 * sep + 0.05 * coh
    elif kind == "formation":
        # move so that (me - offset_me) agrees with the neighbours' (them - offset_them)
        if not nbrs:
            return vel * 0.9
        centre = np.mean(
            [q - formation_offsets[j] for j, q in nbrs.items()] + [p - formation_offsets[i]], axis=0
        )
        v = (centre + formation_offsets[i]) - p
    elif kind == "rendezvous":
        if not nbrs:
            return vel * 0.9
        v = np.mean(list(nbrs.values()), axis=0) - p
    elif kind == "coverage":
        cells = coverage_grid.shape[0]
        cx, cy = np.meshgrid(np.arange(cells), np.arange(cells), indexing="ij")
        centres = (np.stack([cx, cy], axis=-1) + 0.5) * (arena / cells)
        occupied = {tuple(np.clip((q / arena * cells).astype(int), 0, cells - 1)) for q in nbrs.values()}
        score = coverage_grid.astype(float) + 0.0
        for c in occupied:
            score[c] += 5.0
        score += np.linalg.norm(centres - p, axis=2) / arena  # nearer is better among the least covered
        best = np.unravel_index(np.argmin(score), score.shape)
        v = centres[best] - p
    else:  # goto
        v = (target - p) if target is not None else -vel
    sp = np.linalg.norm(v)
    if sp > max_speed:
        v = v / sp * max_speed
    return v


PALETTE = [
    "#2563eb",
    "#dc2626",
    "#16a34a",
    "#d97706",
    "#7c3aed",
    "#0891b2",
    "#db2777",
    "#65a30d",
    "#ea580c",
    "#4f46e5",
    "#0d9488",
    "#b91c1c",
]


def paths_svg(agents_file: Path, arena: float, size: int = 520) -> str:
    """Top-down plot of every agent's path (one colour each, a dot at the end)."""
    paths: dict[str, list[tuple[float, float]]] = {}
    for line in agents_file.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            paths.setdefault(str(r["id"]), []).append((r["x"], r["y"]))
        except (ValueError, KeyError):
            continue
    pad = 24
    sc = (size - 2 * pad) / max(arena, 1e-6)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}"><rect width="100%" height="100%" fill="white"/>'
    ]
    parts.append(
        f'<rect x="{pad}" y="{pad}" width="{size - 2 * pad}" height="{size - 2 * pad}" fill="none" stroke="#ddd"/>'
    )
    for k, (aid, pts) in enumerate(sorted(paths.items(), key=lambda kv: int(kv[0]))):
        c = PALETTE[k % len(PALETTE)]
        poly = " ".join(f"{pad + x * sc:.1f},{size - pad - y * sc:.1f}" for x, y in pts)
        parts.append(f'<polyline points="{poly}" fill="none" stroke="{c}" stroke-width="1.2" opacity="0.8"/>')
        x, y = pts[-1]
        parts.append(
            f'<circle cx="{pad + x * sc:.1f}" cy="{size - pad - y * sc:.1f}" r="4" fill="{c}"/><text x="{pad + x * sc + 5:.1f}" y="{size - pad - y * sc - 5:.1f}" font-size="10" fill="{c}">{aid}</text>'
        )
    parts.append(
        f'<text x="{pad}" y="16" font-size="12" fill="#444">{len(paths)} agents · arena {arena:g} m · dot = final position</text></svg>'
    )
    return "".join(parts)


def series_svg(series: dict[str, list], dt: float, width: int = 560, height: int = 300) -> str:
    """Formation error, consensus error, belief error and coverage over time on one chart (normalised per series)."""
    keys = [
        k
        for k in ("formation_error", "consensus_error", "belief_error", "coverage", "mean_neighbours")
        if series.get(k)
    ]
    pad_l, pad_r, pad_t, pad_b = 40, 12, 20, 28
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="white"/>'
    ]
    parts.append(f'<rect x="{pad_l}" y="{pad_t}" width="{w}" height="{h}" fill="none" stroke="#ddd"/>')
    for k, key in enumerate(keys):
        vals = series[key]
        top = max(vals) or 1.0
        n = len(vals)
        pts = " ".join(
            f"{pad_l + i / max(n - 1, 1) * w:.1f},{pad_t + h - v / top * h:.1f}" for i, v in enumerate(vals)
        )
        c = PALETTE[k % len(PALETTE)]
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{c}" stroke-width="1.5"/>')
        parts.append(
            f'<text x="{pad_l + 6}" y="{pad_t + 14 + 13 * k}" font-size="11" fill="{c}">{key} (max {top:.2f})</text>'
        )
    ticks = series[keys[0]] if keys else []
    parts.append(
        f'<text x="{pad_l}" y="{height - 8}" font-size="11" fill="#666">t = 0 … {len(ticks) * dt:.0f} s · each series scaled to its own max</text></svg>'
    )
    return "".join(parts)


def rules_policy(view: dict) -> tuple[str, float]:
    """Baseline: separation first, then the mission behaviour, then rendezvous when isolated."""
    if view["min_neighbour_dist"] is not None and view["min_neighbour_dist"] < view["collision_r"] * 2:
        return "flock", 0.9
    if view["n_neighbours"] == 0 and view["tick"] > 20:
        return "rendezvous", 0.7
    if view["mission"] == "coverage" and view["coverage_done"] > 0.9:
        return "formation", 0.6
    return view["mission"], 0.8


def run(a: argparse.Namespace) -> dict:
    """Simulate and write the outputs."""
    if a.plans_dir:
        world.EXTRA_PLAN_DIRS[:] = [Path(a.plans_dir)]
    rnd = random.Random(a.seed)
    rng = np.random.default_rng(a.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    n, arena = a.agents, a.arena
    cur = None
    if getattr(a, "current", ""):
        try:
            parts = [float(v) for v in str(a.current).split(",")]
        except ValueError as e:
            raise SystemExit(f"--current must be 'vx,vy' in m/s, got {a.current!r}") from e
        if len(parts) != 2:
            raise SystemExit(f"--current must be 'vx,vy' in m/s, got {a.current!r}")
        cur = (parts[0], parts[1])
    medium = medium_named(getattr(a, "medium", "air"), cur)
    lim = limits_for(getattr(a, "vehicle", "point"), a.max_speed, a.max_accel)
    veh_heading = np.zeros(n)
    if medium.name != "air" or lim.model != "point":
        print(f"medium {medium.name}: {medium.note} · vehicle {lim.model}", flush=True)
    sense_range = a.sense_range * medium.sense_scale
    obstacles = (
        Obstacles.from_json(a.obstacles)
        if getattr(a, "obstacles", "")
        else Obstacles.layout_named(getattr(a, "layout", "none"), arena)
    )
    if obstacles:
        obstacles.save(out / "obstacles.json")
    pos = rng.uniform(0.1 * arena, 0.9 * arena, size=(n, 2))
    for i in range(n):  # spawn outside the obstacles
        for _ in range(200):
            if obstacles.signed_distance(pos[i])[0] > a.collision_r:
                break
            pos[i] = rng.uniform(0.1 * arena, 0.9 * arena, size=2)
    vel = np.zeros((n, 2))
    if lim.min_speed > 0:  # a fixed-wing is launched already flying, on a random heading
        veh_heading = rng.uniform(-math.pi, math.pi, size=n)
        vel = np.stack([np.cos(veh_heading), np.sin(veh_heading)], axis=1) * lim.min_speed
    belief = pos.copy()
    kfs = (
        [
            KalmanBelief(pos[i] + rng.normal(0, a.obs_noise, 2), q=a.kf_q, r=max(a.obs_noise, 1e-3))
            for i in range(n)
        ]
        if a.belief == "kf"
        else None
    )
    ekfs = (
        [
            UnicycleEKF(pos[i] + rng.normal(0, a.obs_noise, 2), 0.0, a.odom_noise, max(a.obs_noise, 1e-3))
            for i in range(n)
        ]
        if a.belief == "ekf"
        else None
    )
    heading = np.zeros(n)  # true headings (from the velocity), for the odometry and the bearing measurements
    rb_updates = 0
    belief_err: list[float] = []
    ring = np.linspace(0, 2 * math.pi, n, endpoint=False)
    offsets = np.stack([np.cos(ring), np.sin(ring)], axis=1) * a.formation_radius
    cells = a.coverage_cells
    grid = np.zeros((cells, cells), dtype=int)  # the true visit map, for the coverage metric
    shared_map = bool(getattr(a, "shared_map", 1))
    # with shared_map 0 each agent explores on its own visit map, built from its own position belief; with 1
    # (the default of the older experiments) every agent reads the true map, a centrally coordinated exploration
    own_grid = None if shared_map else np.zeros((n, cells, cells), dtype=int)
    targets = rng.uniform(0.1 * arena, 0.9 * arena, size=(n, 2))
    order = list(range(n))  # greedy allocation: each agent takes the nearest free target
    assigned: list[np.ndarray | None] = [None] * n
    free = list(range(n))
    for i in order:
        if not free:
            break
        j = min(free, key=lambda k: float(np.linalg.norm(targets[k] - pos[i])))
        assigned[i] = targets[j]
        free.remove(j)
    comms = Comms(
        n,
        a.comm_range * medium.comm_range_scale,
        min(1.0, a.msg_loss + medium.loss_add),
        a.msg_latency + medium.latency_add,
        rnd,
        los=obstacles if getattr(a, "comms", "range") == "los" else None,
        budget=int(getattr(a, "msg_budget", 0) or 0),
    )
    hits_total = 0
    holds = np.zeros(n, dtype=int)  # ticks on which the connectivity floor overrode the mission, per agent
    board = (
        TaskBoard(
            n, arena, rng, rnd,
            task_count=getattr(a, "task_count", 6), task_rate=getattr(a, "task_rate", 0.0), task_size_ticks=max(1, int(round(getattr(a, "task_size", 2.0) / a.dt))),
            deadline_ticks=int(round(a.deadline / a.dt)) if getattr(a, "deadline", 0.0) > 0 else None, task_radius=getattr(a, "task_radius", 1.0),
            sense_range=sense_range, msg_loss=min(1.0, a.msg_loss + medium.loss_add), msg_latency=a.msg_latency + medium.latency_add,
            policy=getattr(a, "alloc", "greedy"), ticks=a.ticks, dt=a.dt,
        )
        if getattr(a, "mission", "formation") == "tasks"
        else None
    )
    task_snaps: list[dict] = []
    behaviour = [a.mission] * n
    conf = [1.0] * n
    src = ["init"] * n
    f_agents = (out / "agents.jsonl").open("w", encoding="utf-8")
    f_comms = (out / "comms.jsonl").open("w", encoding="utf-8")
    f_dec = (out / "decisions.jsonl").open("w", encoding="utf-8")
    series: dict[str, list] = {
        "formation_error": [],
        "consensus_error": [],
        "coverage": [],
        "mean_neighbours": [],
        "collisions": [],
        "edges": [],
        "components": [],
        "lambda2": [],
        "belief_error": [],
        "blocked_links": [],
        "obstacle_hits": [],
    }
    decisions_by_source: dict[str, int] = {}
    collisions_total = 0
    t0 = time.time()
    for tick in range(a.ticks):
        if board is not None:
            board.arrive(tick)
            board.sense(tick, pos)
        obs = pos + rng.normal(0, a.obs_noise, size=pos.shape)
        if kfs is not None:
            for i in range(n):
                kfs[i].predict(a.dt)
                kfs[i].update(obs[i])
                if (
                    a.fuse_neighbours
                ):  # neighbours report where they saw us (their fix of us = our truth + their noise)
                    for j in comms.neighbours(i, tick - 1, a.stale_after):
                        kfs[i].update(pos[i] + rng.normal(0, a.obs_noise * 2, 2), r=a.obs_noise * 2)
            belief = np.array([k.pos for k in kfs])
        elif ekfs is not None:
            # odometry from the true motion (speed + turn rate) with noise; the fix only every fix_every ticks;
            # range/bearing to every neighbour whose belief we hold (their broadcast position = the landmark)
            speed = np.linalg.norm(vel, axis=1)
            new_heading = np.where(speed > 1e-6, np.arctan2(vel[:, 1], vel[:, 0]), heading)
            turn = (
                np.array(
                    [UnicycleEKF._wrap(float(h1 - h0)) for h0, h1 in zip(heading, new_heading, strict=True)]
                )
                / a.dt
            )
            heading = new_heading
            for i in range(n):
                ekfs[i].predict(
                    float(speed[i]) * (1 + rng.normal(0, a.odom_noise)),
                    float(turn[i]) + rng.normal(0, a.odom_noise),
                    a.dt,
                )
                if (
                    tick % a.fix_every == 0 or i < a.anchors
                ):  # anchors: well-localised agents (a fix every tick)
                    ekfs[i].update_fix(obs[i])
                if a.fuse_neighbours:
                    for j, lm in comms.neighbours(i, tick - 1, a.stale_after).items():
                        # the landmark is j's *belief* of itself (what it broadcast), so j's own uncertainty
                        # enters the measurement noise (range directly, bearing as sigma / range); and we only
                        # listen to neighbours that know their position better than we know ours, which
                        # keeps the correlated-error feedback loop (see docs/WORLDS.md) from building up
                        sig_lm = math.hypot(ekfs[j].pos_sigma, a.max_speed * a.dt)  # its belief, one tick old
                        if sig_lm >= ekfs[i].pos_sigma:
                            continue
                        if (
                            medium.rb_dropout and rng.random() < medium.rb_dropout
                        ):  # fog: the measurement is missed
                            continue
                        dxy = pos[j] - pos[i]
                        rng_true, brg_true = (
                            float(np.linalg.norm(dxy)),
                            float(math.atan2(dxy[1], dxy[0]) - heading[i]),
                        )
                        sig_r, sig_b = (
                            a.range_noise * medium.range_noise_scale,
                            math.radians(a.bearing_noise_deg),
                        )
                        z_r, z_b = rng_true + rng.normal(0, sig_r), brg_true + rng.normal(0, sig_b)
                        ok = (
                            ekfs[i].update_range_bearing_ci(lm, z_r, z_b, sig_r, sig_b, sig_lm)
                            if a.rb_fusion == "ci"
                            else ekfs[i].update_range_bearing(
                                lm,
                                z_r,
                                z_b,
                                math.hypot(sig_r, sig_lm),
                                math.hypot(sig_b, sig_lm / max(rng_true, 1.0)),
                            )
                        )
                        if ok:
                            rb_updates += 1
            belief = np.array([k.pos for k in ekfs])
        else:
            belief = a.belief_alpha * obs + (1 - a.belief_alpha) * (belief + vel * a.dt)
        belief_err.append(float(np.mean(np.linalg.norm(belief - pos, axis=1))))
        comms.step(tick, pos, belief)
        if board is not None:
            board.exchange(tick, comms.links_used)
        d = np.linalg.norm(pos[:, None, :] - pos[None, :, :], axis=2) + np.eye(n) * 1e9
        min_d = d.min(axis=1)
        collisions = int((d < a.collision_r).sum() // 2)
        collisions_total += collisions
        centroid = belief.mean(axis=0)
        form_err = float(np.mean(np.linalg.norm((pos - centroid) - offsets, axis=1)))
        cons_err = float(np.mean(np.linalg.norm(pos - pos.mean(axis=0), axis=1)))
        for i in range(n):
            c = tuple(np.clip((pos[i] / arena * cells).astype(int), 0, cells - 1))
            grid[c] += 1
            if own_grid is not None:
                cb = tuple(np.clip((belief[i] / arena * cells).astype(int), 0, cells - 1))
                own_grid[i][cb] += 1
        coverage = float((grid > 0).mean())
        views = []
        for i in range(n):
            nb = comms.neighbours(i, tick, a.stale_after)
            views.append(
                {
                    "id": i,
                    "tick": tick,
                    "n_neighbours": len(nb),
                    "min_neighbour_dist": round(float(min_d[i]), 2) if min_d[i] < 1e8 else None,
                    "collision_r": a.collision_r,
                    "mission": a.mission,
                    "coverage_done": round(coverage, 3),
                    "formation_error": round(form_err, 3),
                    "speed": round(float(np.linalg.norm(vel[i])), 2),
                }
            )
        if board is not None:
            if tick % a.decision_every == 0:
                board.decide(tick, pos if board.policy == "oracle" else belief, a, [comms.neighbours(i, tick, a.stale_after) for i in range(n)])
                for i in range(n):
                    decisions_by_source[f"alloc:{board.policy}"] = decisions_by_source.get(f"alloc:{board.policy}", 0) + 1
                    c = board.claims[i]
                    f_dec.write(json.dumps({"tick": tick, "agent": i, "behaviour": "goto" if c else "coverage", "task": c.task if c else None, "confidence": 1.0, "source": f"alloc:{board.policy}"}) + "\n")
            for i in range(n):
                assigned[i] = board.target(i)
                behaviour[i], conf[i], src[i] = ("goto" if assigned[i] is not None else "coverage"), 1.0, f"alloc:{board.policy}"
        elif tick % a.decision_every == 0:
            if a.policy in EXTRA_POLICIES:
                try:
                    picks = [
                        (str(b), float(c), str(src_)) for b, c, src_ in EXTRA_POLICIES[a.policy](views, a)
                    ]
                    if len(picks) != len(views) or any(b not in BEHAVIOURS for b, _, _ in picks):
                        raise ValueError("a policy must return one known behaviour per view")
                except Exception as e:  # noqa: BLE001 - the swarm must keep moving
                    print(
                        f"tick {tick}: policy {a.policy} unavailable ({str(e)[:80]}); rules for this tick",
                        flush=True,
                    )
                    picks = [(*rules_policy(v), "rules-fallback") for v in views]
            elif a.policy == "random":
                picks = [(rnd.choice(BEHAVIOURS), 0.2, "random") for _ in views]
            else:
                picks = [(*rules_policy(v), "rules") for v in views]
            for i, (b, cf, s) in enumerate(picks):
                behaviour[i], conf[i], src[i] = b, cf, s
                decisions_by_source[s] = decisions_by_source.get(s, 0) + 1
                f_dec.write(
                    json.dumps({"tick": tick, "agent": i, "behaviour": b, "confidence": cf, "source": s})
                    + "\n"
                )
        new_vel = np.zeros_like(vel)
        for i in range(n):
            nb = comms.neighbours(i, tick, a.stale_after)
            v = behaviour_velocity(
                behaviour[i], i, belief[i], vel[i], nb, offsets, grid if own_grid is None else own_grid[i], assigned[i], arena, a.max_speed
            )
            if getattr(a, "lambda2_floor", 0.0) > 0 and nb:
                # planning under a constraint: if the move would drop the connectivity I can see under the
                # floor, hold the mission and move toward my neighbours instead (the swarm's true λ2 is measured)
                others = list(nb.values())
                ahead = belief[i] + v * a.dt * getattr(a, "lookahead", 5.0)
                if local_lambda2(ahead, others, comms.range) < a.lambda2_floor:
                    centre = np.mean(others, axis=0)
                    pull = centre - belief[i]
                    sp = np.linalg.norm(pull)
                    v = pull / sp * a.max_speed if sp > 1e-6 else -vel[i]
                    holds[i] += 1
            if (
                obstacles
            ):  # sensed obstacles push back (on the true position: a proximity sensor, not the belief)
                v = v + a.max_speed * 0.5 * obstacles.repulsion(pos[i], sense_range)
                sp = np.linalg.norm(v)
                if sp > a.max_speed:
                    v = v / sp * a.max_speed
            new_vel[i], veh_heading[i] = track(
                v, vel[i], float(veh_heading[i]), lim, a.dt
            )  # the vehicle model follows the demand
        vel = apply_drag(new_vel, medium, a.dt, lim.min_speed)
        pos = pos + (vel + np.array(medium.current)) * a.dt  # the current carries everyone
        hits = 0
        if obstacles:
            for i in range(n):
                pos[i], moved = obstacles.push_out(pos[i], margin=a.collision_r / 2, arena=arena)
                if moved:
                    hits += 1
                    vel[i] *= 0.2  # a wall stops you
        pos = np.clip(pos, 0, arena)  # the arena edge last, so a push-out can never leave it
        hits_total += hits
        if board is not None:
            board.serve(tick, pos, vel, a.dt)
            task_snaps.append(board.snapshot(tick))
        series["formation_error"].append(round(form_err, 4))
        series["consensus_error"].append(round(cons_err, 4))
        series["coverage"].append(round(coverage, 4))
        series["mean_neighbours"].append(round(float(np.mean([v["n_neighbours"] for v in views])), 3))
        series["collisions"].append(collisions)
        series["edges"].append(len(comms.edges))
        components, lambda2 = graph_connectivity(n, comms.edges)
        series["components"].append(components)
        series["lambda2"].append(round(lambda2, 4))
        series["belief_error"].append(round(belief_err[-1], 4))
        series["blocked_links"].append(comms.blocked)
        series["obstacle_hits"].append(hits)
        t = tick * a.dt
        for i in range(n):
            f_agents.write(
                json.dumps(
                    {
                        "t": round(t, 3),
                        "id": i,
                        "x": round(float(pos[i][0]), 4),
                        "y": round(float(pos[i][1]), 4),
                        "vx": round(float(vel[i][0]), 4),
                        "vy": round(float(vel[i][1]), 4),
                        "b": behaviour[i],
                        "nb": views[i]["n_neighbours"],
                        "bx": round(float(belief[i][0]), 3),
                        "by": round(float(belief[i][1]), 3),
                        "sig": round((kfs[i] if kfs is not None else ekfs[i]).pos_sigma, 3)
                        if (kfs is not None or ekfs is not None)
                        else None,
                    }
                )
                + "\n"
            )
        f_comms.write(
            json.dumps(
                {
                    "t": round(t, 3),
                    "edges": comms.edges,
                    "sent": comms.sent,
                    "lost": comms.lost,
                    "components": components,
                    "lambda2": round(lambda2, 4),
                    "blocked": comms.blocked,
                }
            )
            + "\n"
        )
        if tick % 50 == 0:
            print(
                f"tick {tick}/{a.ticks} · edges {len(comms.edges)} · coverage {coverage:.2f} · formation err {form_err:.2f}",
                flush=True,
            )
            f_agents.flush()
        if a.rate > 0:
            time.sleep(1.0 / a.rate)
    for f in (f_agents, f_comms, f_dec):
        f.close()
    last = {k: v[-1] for k, v in series.items() if v}
    metrics = {
        "headline": {
            "formation_error_m": last["formation_error"],
            "belief_error_m": round(float(np.mean(belief_err[-50:])), 4),
            "coverage_pct": round(100 * last["coverage"], 1),
            "consensus_error_m": last["consensus_error"],
            "collisions": collisions_total,
            "msg_loss_pct": round(100 * comms.lost / max(1, comms.sent), 1),
            "mean_neighbours": round(float(np.mean(series["mean_neighbours"])), 2),
            "connected_pct": round(100 * float(np.mean([c == 1 for c in series["components"]])), 1),
            "mean_lambda2": round(float(np.mean(series["lambda2"])), 3),
            "blocked_links_pct": round(
                100
                * float(sum(series["blocked_links"]))
                / max(1, sum(series["blocked_links"]) + sum(series["edges"])),
                1,
            ),
            "obstacle_hits": hits_total,
            "connectivity_holds_pct": round(100 * float(holds.sum()) / max(1, n * a.ticks), 1),
        },
        "series": series,
        "tasks": board.metrics(a.dt) if board is not None else None,
        "messages": {"sent": comms.sent, "lost": comms.lost, "range_bearing_updates": rb_updates},
        "decisions_by_source": decisions_by_source,
        "agents": n,
        "ticks": a.ticks,
        "seconds": round(time.time() - t0, 1),
        "params": {k: v for k, v in vars(a).items() if k != "out"},
        "obstacles": obstacles.to_dict() if obstacles else None,
        "medium": medium.to_dict(),
        "vehicle": lim.model,
    }
    if board is not None:
        tm = board.metrics(a.dt)
        metrics["headline"].update({k: v for k, v in tm.items() if v is not None and k not in ("conflict_ticks", "task_msgs_lost")})
        board.write(out / "tasks.jsonl", task_snaps)
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    (out / "trajectory.svg").write_text(paths_svg(out / "agents.jsonl", arena), encoding="utf-8")
    (out / "metrics.svg").write_text(series_svg(series, a.dt), encoding="utf-8")
    print(
        f"done: {n} agents, {a.ticks} ticks, {comms.sent} messages ({comms.lost} lost), {collisions_total} collisions, coverage {last['coverage']:.2f}",
        flush=True,
    )
    return metrics


def main(argv: list[str] | None = None) -> int:
    """CLI."""
    for stream in (sys.stdout, sys.stderr):  # a Windows console may not be UTF-8
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--agents", type=int, default=12)
    ap.add_argument("--ticks", type=int, default=400)
    ap.add_argument("--dt", type=float, default=0.1)
    ap.add_argument("--arena", type=float, default=40.0, help="side of the square arena (m)")
    ap.add_argument("--mission", choices=BEHAVIOURS, default="formation")
    ap.add_argument(
        "--policy",
        choices=["rules", "random", *EXTRA_POLICIES],
        default="rules",
        help="who picks each agent's behaviour every decision interval: rules (separation, mission, rendezvous when isolated) or random (a control)",
    )
    ap.add_argument(
        "--decision-every", dest="decision_every", type=int, default=10, help="ticks between decisions"
    )
    ap.add_argument("--comm-range", dest="comm_range", type=float, default=10.0)
    ap.add_argument("--msg-budget", dest="msg_budget", type=int, default=0, help="messages an agent may send per tick; 0 = one to every neighbour")
    ap.add_argument("--lambda2-floor", dest="lambda2_floor", type=float, default=0.0, help="hold the mission when the agent's own estimate of connectivity would drop under this; 0 = off")
    ap.add_argument("--lookahead", type=float, default=5.0, help="ticks ahead the connectivity check looks")
    ap.add_argument(
        "--comms",
        choices=["range", "los"],
        default="range",
        help="range: any pair within comm_range talks; los: the pair also needs line of sight (walls block radio)",
    )
    ap.add_argument(
        "--plans-dir", default="", help="a folder with the person's own floor plans (plan:<name>)"
    )
    ap.add_argument(
        "--layout",
        type=_layout_arg,
        default="none",
        help="obstacles: pillars (four round pillars), rooms (four rooms with doors), plan:<name> (an ASCII floor plan from simlab/plans), corridor (a corridor across the middle)",
    )
    ap.add_argument(
        "--obstacles",
        default="",
        help='custom obstacles as JSON: {"circles": [[cx, cy, r]], "boxes": [[x, y, w, h]]} (overrides --layout)',
    )
    ap.add_argument(
        "--sense-range",
        dest="sense_range",
        type=float,
        default=3.0,
        help="metres within which an agent senses an obstacle and is pushed away",
    )
    ap.add_argument(
        "--medium",
        choices=MEDIA,
        default="air",
        help="air (nothing) | water (drag, a current, acoustic links: half range, +5 ticks, +20 %% loss) | fog (sensing: obstacles seen at 40 %%, half the range/bearing measurements missed, the rest twice as noisy)",
    )
    ap.add_argument("--current", default="", help="water: the current as 'vx,vy' in m/s (default 0.3,0)")
    ap.add_argument(
        "--vehicle",
        choices=VEHICLES,
        default="point",
        help="point (mass with speed + accel limits) | unicycle (no sideways motion, turn-rate limit) | fixed-wing (unicycle that must keep moving, wide turns) | quadrotor-lite (slower to respond, a little drag)",
    )
    ap.add_argument(
        "--msg-loss", dest="msg_loss", type=float, default=0.0, help="probability a message is lost"
    )
    ap.add_argument(
        "--msg-latency", dest="msg_latency", type=int, default=0, help="ticks a message takes to arrive"
    )
    ap.add_argument(
        "--stale-after",
        dest="stale_after",
        type=int,
        default=20,
        help="ticks after which a neighbour belief is ignored",
    )
    ap.add_argument(
        "--obs-noise", dest="obs_noise", type=float, default=0.3, help="self-localisation noise sigma (m)"
    )
    ap.add_argument(
        "--belief-alpha",
        dest="belief_alpha",
        type=float,
        default=0.3,
        help="belief filter gain (1 = trust the noisy observation fully)",
    )
    ap.add_argument(
        "--belief",
        choices=["exp", "kf", "ekf"],
        default="exp",
        help="belief filter: exp (exponential), kf (constant-velocity Kalman on fixes) or ekf (unicycle EKF on odometry + fixes + range/bearing to neighbours)",
    )
    ap.add_argument(
        "--odom-noise",
        dest="odom_noise",
        type=float,
        default=0.1,
        help="ekf: relative odometry noise (speed and turn rate)",
    )
    ap.add_argument(
        "--fix-every",
        dest="fix_every",
        type=int,
        default=1,
        help="ekf: an own position fix every N ticks (10 = GPS mostly missing; range/bearing to neighbours must carry the belief)",
    )
    ap.add_argument(
        "--anchors",
        type=int,
        default=0,
        help="ekf: the first N agents get a position fix every tick (well-localised anchors the rest can range against)",
    )
    ap.add_argument(
        "--rb-fusion",
        dest="rb_fusion",
        choices=["ci", "naive"],
        default="ci",
        help="ekf: how range/bearing to neighbours is fused: ci (covariance intersection, consistent under correlation) or naive (plain EKF update; shows the correlated-error trap)",
    )
    ap.add_argument(
        "--range-noise",
        dest="range_noise",
        type=float,
        default=0.2,
        help="ekf: range measurement noise sigma (m)",
    )
    ap.add_argument(
        "--bearing-noise-deg",
        dest="bearing_noise_deg",
        type=float,
        default=3.0,
        help="ekf: bearing measurement noise sigma (deg)",
    )
    ap.add_argument("--kf-q", dest="kf_q", type=float, default=0.5, help="Kalman process noise scale")
    ap.add_argument(
        "--fuse-neighbours",
        dest="fuse_neighbours",
        type=int,
        default=0,
        help="1 = fuse neighbours into the belief: kf → their fixes of this agent; ekf → range/bearing to their broadcast positions (cooperative localisation)",
    )
    ap.add_argument("--formation-radius", dest="formation_radius", type=float, default=6.0)
    ap.add_argument("--coverage-cells", dest="coverage_cells", type=int, default=10)
    ap.add_argument("--max-speed", dest="max_speed", type=float, default=2.0)
    ap.add_argument("--max-accel", dest="max_accel", type=float, default=4.0)
    ap.add_argument("--collision-r", dest="collision_r", type=float, default=0.8)
    ap.add_argument(
        "--rate",
        type=float,
        default=0.0,
        help="ticks per second to emit (0 = as fast as possible); >0 paces a run for a live viewer",
    )
    ap.add_argument("--alloc", choices=[*ALLOC_POLICIES, *task_alloc.EXTRA_ALLOC], default="greedy", help="task allocation policy for --mission tasks")
    ap.add_argument("--task-count", dest="task_count", type=int, default=6, help="tasks present at the start")
    ap.add_argument("--task-rate", dest="task_rate", type=float, default=0.0, help="new tasks per 100 ticks (Poisson); 0 = none")
    ap.add_argument("--task-size", dest="task_size", type=float, default=2.0, help="seconds an agent must stay at a task to serve it")
    ap.add_argument("--deadline", type=float, default=0.0, help="seconds after arrival before a task is missed; 0 = no deadline")
    ap.add_argument("--task-radius", dest="task_radius", type=float, default=1.0, help="metres within which an agent is at a task")
    ap.add_argument("--shared-map", dest="shared_map", type=int, default=1, help="1: every agent explores on the true shared visit map (the older experiments); 0: each agent keeps its own map from its own position belief")
    ap.add_argument("--seed", type=int, default=7)
    for hook in EXTRA_ARGUMENTS:
        hook(ap)
    run(ap.parse_args(argv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
