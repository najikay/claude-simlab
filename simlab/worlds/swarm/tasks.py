"""Distributed task allocation for the swarm world (mission ``tasks``).

Tasks appear in the arena (a position, a service time, a deadline). An agent learns of a task only by
sensing it within ``sense_range`` or by hearing about it from a neighbour; task news and claims travel
over the same links as everything else, with the same loss and latency. Every ``decision_every`` ticks
each agent picks a task by the allocation policy:

``greedy``   the nearest task it knows is open (it may collide with a neighbour's choice)
``yield``    greedy, plus one rule and no bids: an agent drops its task when a neighbour whose position it
             holds is nearer to that task than itself (the give-way without the auction; the straw-man check)
``auction``  bid = distance; a claim is broadcast; an agent that hears a lower bid on its task drops it
             and picks again (lowest bid wins, ties by id: a one-round distributed auction)
``cbaa``     the consensus-based auction of Choi, Brunet and How (IEEE T-RO 2009), single-assignment:
             each agent keeps the winning bid and winner it knows for every task, bids where its own
             bid beats the winner, and neighbours merge their lists every tick (the better bid wins,
             ties by lower id); on a connected graph the assignment is conflict-free after at most the
             graph's diameter rounds. Bid = negative distance, the same currency as ``auction``
``oracle``   a central Hungarian assignment over the true state every decision tick (sees everything,
             sends nothing): the upper bound
``random``   a random known open task: the control

A task is served when an agent stays within ``task_radius`` of it for ``task_size`` seconds; a task whose
deadline passes unserved is missed. Two agents committed to one task at the same tick is a conflict.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

POLICIES = ["greedy", "yield", "auction", "cbaa", "oracle", "random"]
# an embedding project can add an allocation policy without forking (the workbench adds "laya"):
# name -> callable(views, args) -> one chosen task id (or None) per view, in order. A view carries the
# agent's id and position and the open tasks it knows (id, distance, seconds to the deadline, the best
# bid it has heard). On any error the board falls back to greedy for that decision.
EXTRA_ALLOC: dict = {}


def hungarian(cost: np.ndarray) -> list[tuple[int, int]]:
    """Minimum-cost assignment of rows to columns (Kuhn-Munkres with potentials, O(n^2 m)).

    Rectangular matrices are fine: every row of the smaller side is matched. Pure numpy, so the
    plugin needs no scipy. Returns (row, col) pairs.
    """
    a = np.asarray(cost, dtype=float)
    transposed = a.shape[0] > a.shape[1]
    if transposed:
        a = a.T
    n, m = a.shape
    inf = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)  # column -> row (1-based), 0 = free
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta, j1 = inf, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = a[i0 - 1, j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    pairs = [(p[j] - 1, j - 1) for j in range(1, m + 1) if p[j]]
    return [(c, r) for r, c in pairs] if transposed else pairs


@dataclass
class Task:
    id: int
    pos: np.ndarray
    size_ticks: int
    arrival: int
    deadline: int | None  # tick, or None for no deadline
    served_at: int | None = None
    missed_at: int | None = None
    served_by: int | None = None
    progress: dict[int, int] = field(default_factory=dict)  # agent -> service ticks accumulated

    @property
    def open(self) -> bool:
        return self.served_at is None and self.missed_at is None


@dataclass
class Claim:
    task: int
    bid: float
    since: int


class TaskBoard:  # noqa: PLR0902 - the state of one mission
    """Tasks, what each agent knows, claims, and the counts."""

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        n: int,
        arena: float,
        rng: np.random.Generator,
        rnd: random.Random,
        *,
        task_count: int,
        task_rate: float,
        task_size_ticks: int,
        deadline_ticks: int | None,
        task_radius: float,
        sense_range: float,
        msg_loss: float,
        msg_latency: int,
        policy: str,
        ticks: int,
        dt: float = 0.1,
    ) -> None:
        if policy not in POLICIES and policy not in EXTRA_ALLOC:
            raise ValueError(f"alloc must be one of {POLICIES + list(EXTRA_ALLOC)}")
        self.dt = dt
        self.n, self.arena, self.rng, self.rnd = n, arena, rng, rnd
        self.rate, self.size, self.deadline = task_rate, max(1, task_size_ticks), deadline_ticks
        self.radius, self.sense_range, self.loss, self.latency = task_radius, sense_range, msg_loss, msg_latency
        self.policy, self.ticks = policy, ticks
        self.tasks: list[Task] = []
        self.known: list[dict[int, int]] = [{} for _ in range(n)]  # agent -> {task id: tick heard}
        self.heard_served: list[set[int]] = [set() for _ in range(n)]
        self.heard_bids: list[dict[int, tuple[float, int]]] = [{} for _ in range(n)]  # task -> (bid, agent)
        self.winners: list[dict[int, tuple[float, int]]] = [{} for _ in range(n)]  # cbaa: task -> (bid, winner) as this agent knows it
        self.claims: list[Claim | None] = [None] * n
        self.queue: list[tuple[int, int, dict]] = []  # (deliver_at, dst, news)
        self.msgs_sent = 0
        self.msgs_lost = 0
        self.conflict_ticks = 0
        self.conflicts = 0
        self.blind_task_ticks = 0  # open tasks known to no agent, summed over ticks (the mechanism check, V4)
        self.open_task_ticks = 0
        self.known_per_agent_sum = 0.0
        self.distance = np.zeros(n)
        self._next_id = 0
        for _ in range(task_count):
            self._spawn(0)

    # -- the world --------------------------------------------------------------------------
    def _spawn(self, tick: int) -> Task:
        t = Task(
            id=self._next_id,
            pos=self.rng.uniform(0.1 * self.arena, 0.9 * self.arena, size=2),
            size_ticks=self.size,
            arrival=tick,
            deadline=None if self.deadline is None else tick + self.deadline,
        )
        self._next_id += 1
        self.tasks.append(t)
        return t

    def arrive(self, tick: int) -> None:
        """Poisson arrivals: ``task_rate`` tasks per 100 ticks on average."""
        if self.rate > 0 and tick > 0:
            for _ in range(int(self.rng.poisson(self.rate / 100.0))):
                self._spawn(tick)

    def sense(self, tick: int, pos: np.ndarray) -> None:
        """An agent learns of every open task within its sensing range (and sees a served one as served)."""
        for t in self.tasks:
            d = np.linalg.norm(pos - t.pos, axis=1)
            for i in np.nonzero(d <= self.sense_range)[0]:
                if t.open:
                    self.known[i].setdefault(t.id, tick)
                else:
                    self.heard_served[i].add(t.id)

    # -- the links ---------------------------------------------------------------------------
    def exchange(self, tick: int, sends: list[tuple[int, int]]) -> None:
        """Task news (ids I know, ids I know are done, my claim) on every directed send of the tick
        (the same links, budget, loss and latency as the position beliefs)."""
        if self.policy == "oracle":
            return  # the oracle assigns from the true state and reads no message
        for src, dst in sends:
            news = {
                "known": list(self.known[src].keys()),
                "done": list(self.heard_served[src]),
                "claim": (self.claims[src].task, self.claims[src].bid, src) if self.claims[src] else None,
                "winners": dict(self.winners[src]) if self.policy == "cbaa" else None,
            }
            self.msgs_sent += 1
            if self.rnd.random() < self.loss:
                self.msgs_lost += 1
                continue
            self.queue.append((tick + self.latency, dst, news))
        due = [m for m in self.queue if m[0] <= tick]
        self.queue = [m for m in self.queue if m[0] > tick]
        for _, dst, news in due:
            for tid in news["known"]:
                self.known[dst].setdefault(tid, tick)
            self.heard_served[dst].update(news["done"])
            if news["claim"] is not None:
                tid, bid, who = news["claim"]
                best = self.heard_bids[dst].get(tid)
                if best is None or (bid, who) < best:
                    self.heard_bids[dst][tid] = (bid, who)
            if news.get("winners"):
                # max-consensus on the winning bids: keep the better (lower distance, then lower id) per task
                mine = self.winners[dst]
                for tid, (bid, who) in news["winners"].items():
                    cur = mine.get(tid)
                    if cur is None or (bid, who) < cur:
                        mine[tid] = (bid, who)

    # -- the decision ------------------------------------------------------------------------
    def _open_known(self, i: int) -> list[Task]:
        by_id = {t.id: t for t in self.tasks}
        return [by_id[tid] for tid in self.known[i] if tid in by_id and by_id[tid].open and tid not in self.heard_served[i]]

    def views(self, tick: int, pos: np.ndarray) -> list[dict]:
        """What each agent knows, for an external allocation policy."""
        out = []
        for i in range(self.n):
            known = []
            for t in self._open_known(i):
                bid = self.heard_bids[i].get(t.id)
                known.append({
                    "task": t.id,
                    "distance_m": round(float(np.linalg.norm(pos[i] - t.pos)), 1),
                    "deadline_s": None if t.deadline is None else round((t.deadline - tick) * self.dt, 1),
                    "heard_bid_m": None if bid is None else round(bid[0], 1),
                })
            out.append({"id": i, "tick": tick, "x": round(float(pos[i][0]), 1), "y": round(float(pos[i][1]), 1), "claim": self.claims[i].task if self.claims[i] else None, "known": known})
        return out

    def decide(self, tick: int, pos: np.ndarray, args: object | None = None, known_pos: list[dict[int, np.ndarray]] | None = None) -> None:  # noqa: C901
        """Every agent (re)chooses a task by the policy; the oracle assigns everyone at once.
        ``known_pos``: per agent, the neighbour positions it holds (for ``yield``)."""
        self._known_pos = known_pos
        if self.policy in EXTRA_ALLOC:
            views = self.views(tick, pos)
            try:
                picks = list(EXTRA_ALLOC[self.policy](views, args))
                if len(picks) != self.n:
                    raise ValueError("an allocation policy must return one task per agent")
                by_id = {t.id: t for t in self.tasks}
                for i, tid in enumerate(picks):
                    t = by_id.get(tid) if tid is not None else None
                    self.claims[i] = Claim(t.id, float(np.linalg.norm(pos[i] - t.pos)), tick) if t is not None and t.open else None
                return
            except Exception as e:  # noqa: BLE001 - the swarm must keep moving
                print(f"tick {tick}: alloc {self.policy} unavailable ({str(e)[:80]}); greedy for this decision", flush=True)
                saved, self.policy = self.policy, "greedy"
                try:
                    self.decide(tick, pos)
                finally:
                    self.policy = saved
                return
        if self.policy == "oracle":
            open_tasks = [t for t in self.tasks if t.open]
            self.claims = [None] * self.n
            if open_tasks:
                cost = np.array([[np.linalg.norm(pos[i] - t.pos) for t in open_tasks] for i in range(self.n)])
                for r, c in hungarian(cost):
                    self.claims[r] = Claim(open_tasks[c].id, float(cost[r, c]), tick)
            return
        if self.policy == "cbaa":
            self._decide_cbaa(tick, pos)
            return
        for i in range(self.n):
            cur = self.claims[i]
            if cur is not None:
                t = next((x for x in self.tasks if x.id == cur.task), None)
                if t is None or not t.open or cur.task in self.heard_served[i]:
                    cur = None
                elif self.policy == "auction":
                    best = self.heard_bids[i].get(cur.task)
                    if best is not None and (best[0], best[1]) < (cur.bid, i):
                        cur = None  # someone nearer claimed it: give way
                elif self.policy == "yield" and t is not None and self._known_pos:
                    mine = float(np.linalg.norm(pos[i] - t.pos))
                    for j, q in (self._known_pos[i] or {}).items():
                        if (float(np.linalg.norm(q - t.pos)), j) < (mine, i):
                            cur = None  # a neighbour I can see is nearer: give way without a word
                            break
            if cur is not None and self.policy != "random":
                self.claims[i] = cur
                continue
            cands = self._open_known(i)
            if self.policy == "yield" and self._known_pos:
                near = self._known_pos[i] or {}
                cands = [t for t in cands if not any((float(np.linalg.norm(q - t.pos)), j) < (float(np.linalg.norm(pos[i] - t.pos)), i) for j, q in near.items())]
            if self.policy == "auction":
                cands = [
                    t
                    for t in cands
                    if (b := self.heard_bids[i].get(t.id)) is None or (float(np.linalg.norm(pos[i] - t.pos)), i) < b
                ]
            if not cands:
                self.claims[i] = None
                continue
            if self.policy == "random":
                t = cands[self.rnd.randrange(len(cands))]
            else:
                t = min(cands, key=lambda x: float(np.linalg.norm(pos[i] - x.pos)))
            self.claims[i] = Claim(t.id, float(np.linalg.norm(pos[i] - t.pos)), tick)

    def _decide_cbaa(self, tick: int, pos: np.ndarray) -> None:
        """CBAA's bid phase for every agent: drop a task someone else now wins; otherwise keep it; with no
        task, bid on the known open task where my bid beats the winning bid I know of."""
        by_id = {t.id: t for t in self.tasks}
        for i in range(self.n):
            w = self.winners[i]
            for tid in [t for t in w if t not in by_id or not by_id[t].open or t in self.heard_served[i]]:
                del w[tid]  # finished or unknown tasks leave the lists
            cur = self.claims[i]
            if cur is not None:
                win = w.get(cur.task)
                if win is not None and win[1] != i:
                    cur = None  # outbid: someone else is the winner now
                elif cur.task not in by_id or not by_id[cur.task].open:
                    cur = None
            if cur is not None:
                self.claims[i] = cur
                continue
            best_t, best_bid = None, None
            for t in self._open_known(i):
                bid = float(np.linalg.norm(pos[i] - t.pos))
                win = w.get(t.id)
                if win is not None and not ((bid, i) < win):
                    continue  # someone bids better on it
                if best_bid is None or bid < best_bid:
                    best_t, best_bid = t, bid
            if best_t is None:
                self.claims[i] = None
                continue
            w[best_t.id] = (best_bid, i)
            self.claims[i] = Claim(best_t.id, best_bid, tick)

    def target(self, i: int) -> np.ndarray | None:
        c = self.claims[i]
        if c is None:
            return None
        t = next((x for x in self.tasks if x.id == c.task), None)
        return None if t is None or not t.open else t.pos

    # -- the service -------------------------------------------------------------------------
    def serve(self, tick: int, pos: np.ndarray, vel: np.ndarray, dt: float) -> None:
        """Progress on claimed tasks, misses, conflicts, distance."""
        self.distance += np.linalg.norm(vel, axis=1) * dt
        committed: dict[int, list[int]] = {}
        for i, c in enumerate(self.claims):
            if c is not None:
                committed.setdefault(c.task, []).append(i)
        clash = sum(len(v) - 1 for v in committed.values() if len(v) > 1)
        if clash:
            self.conflict_ticks += 1
            self.conflicts += clash
        open_ids = [t.id for t in self.tasks if t.open and t.arrival <= tick]
        if open_ids:
            known_any = set().union(*(set(k) for k in self.known)) if self.n else set()
            self.open_task_ticks += len(open_ids)
            self.blind_task_ticks += sum(1 for tid in open_ids if tid not in known_any)
        self.known_per_agent_sum += sum(len(self._open_known(i)) for i in range(self.n)) / max(1, self.n)
        by_id = {t.id: t for t in self.tasks}
        for tid, agents in committed.items():
            t = by_id.get(tid)
            if t is None or not t.open:
                continue
            for i in agents:
                if np.linalg.norm(pos[i] - t.pos) <= self.radius:
                    t.progress[i] = t.progress.get(i, 0) + 1
                    if t.progress[i] >= t.size_ticks:
                        t.served_at, t.served_by = tick, i
                        for k in range(self.n):
                            if k == i or np.linalg.norm(pos[k] - t.pos) <= self.sense_range:
                                self.heard_served[k].add(tid)
                        break
        for t in self.tasks:
            if t.open and t.deadline is not None and tick >= t.deadline:
                t.missed_at = tick

    # -- the numbers -------------------------------------------------------------------------
    def snapshot(self, tick: int) -> dict:
        return {
            "t": tick,
            "tasks": [
                {
                    "id": t.id,
                    "x": round(float(t.pos[0]), 3),
                    "y": round(float(t.pos[1]), 3),
                    "state": "served" if t.served_at is not None else "missed" if t.missed_at is not None else "claimed" if any(c and c.task == t.id for c in self.claims) else "open",
                }
                for t in self.tasks
                if t.arrival <= tick
            ],
            "claims": [c.task if c else None for c in self.claims],
        }

    def metrics(self, dt: float) -> dict:
        served = [t for t in self.tasks if t.served_at is not None]
        missed = [t for t in self.tasks if t.missed_at is not None]
        times = sorted((t.served_at - t.arrival) * dt for t in served)
        p90 = times[min(len(times) - 1, int(0.9 * len(times)))] if times else None
        return {
            "tasks_total": len(self.tasks),
            "tasks_served": len(served),
            "tasks_missed": len(missed),
            "tasks_open_at_end": len(self.tasks) - len(served) - len(missed),
            "decided_served_pct": round(100 * len(served) / max(1, len(served) + len(missed)), 1),  # among tasks that reached a verdict
            "served_pct": round(100 * len(served) / max(1, len(self.tasks)), 1),
            "service_time_mean_s": round(float(np.mean(times)), 2) if times else None,
            "service_time_p90_s": round(float(p90), 2) if p90 is not None else None,
            "makespan_s": round(max(t.served_at for t in served) * dt, 2) if served else None,
            "conflicts": self.conflicts,
            "conflict_ticks": self.conflict_ticks,
            "blind_pct": round(100 * self.blind_task_ticks / max(1, self.open_task_ticks), 1),
            "known_tasks_per_agent": round(self.known_per_agent_sum / max(1, self.ticks), 2),
            "task_msgs": self.msgs_sent,
            "task_msgs_lost": self.msgs_lost,
            "msgs_per_served_task": round(self.msgs_sent / max(1, len(served)), 1),
            "distance_per_agent_m": round(float(self.distance.mean()), 1),
        }

    def write(self, path: Path, snapshots: list[dict]) -> None:
        with path.open("w", encoding="utf-8") as f:
            for s in snapshots:
                f.write(json.dumps(s) + "\n")
