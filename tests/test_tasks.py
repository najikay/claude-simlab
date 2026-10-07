"""Task allocation (mission ``tasks``): the policies, the service rule, the Hungarian, the outputs."""

import json
import random

import numpy as np
from conftest import load_script

tasks = load_script("simlab/worlds/swarm/tasks.py", "swarm_tasks")


def board(policy, n=2, **kw):
    opts = {
        "task_count": 0, "task_rate": 0.0, "task_size_ticks": 3, "deadline_ticks": None, "task_radius": 1.0,
        "sense_range": 100.0, "msg_loss": 0.0, "msg_latency": 0, "policy": policy, "ticks": 100,
    }
    opts.update(kw)
    return tasks.TaskBoard(n, 40.0, np.random.default_rng(1), random.Random(1), **opts)


def test_hungarian_is_optimal_on_small_cases():
    c = np.array([[4, 1, 3], [2, 0, 5], [3, 2, 2]], float)
    pairs = tasks.hungarian(c)
    assert sorted(pairs) == [(0, 1), (1, 0), (2, 2)] and sum(c[r, k] for r, k in pairs) == 5
    assert tasks.hungarian(np.array([[1, 2, 3, 4], [2, 4, 6, 8]], float)) == [(1, 0), (0, 1)]  # 2 + 2 beats 1 + 4
    assert sorted(tasks.hungarian(np.array([[1, 2], [2, 4], [3, 1]], float))) == [(0, 0), (2, 1)]


def test_greedy_conflicts_where_the_auction_gives_way():
    # two agents, one task between them: greedy sends both; the auction lets the nearer one go
    pos = np.array([[10.0, 10.0], [14.0, 10.0]])
    for policy, expect in (("greedy", 2), ("auction", 1)):
        b = board(policy)
        b.tasks.append(tasks.Task(id=0, pos=np.array([12.5, 10.0]), size_ticks=3, arrival=0, deadline=None))
        edges = [(0, 1)]
        for tick in range(4):
            b.sense(tick, pos)
            b.exchange(tick, [(0, 1), (1, 0)])
            b.decide(tick, pos)
        committed = sum(1 for c in b.claims if c is not None and c.task == 0)
        assert committed == expect, (policy, [c and (c.task, round(c.bid, 2)) for c in b.claims])


def test_news_travels_and_is_lost_like_everything_else():
    pos = np.array([[2.0, 2.0], [30.0, 30.0]])
    b = board("greedy", sense_range=5.0)
    b.tasks.append(tasks.Task(id=0, pos=np.array([3.0, 3.0]), size_ticks=3, arrival=0, deadline=None))
    b.sense(0, pos)
    assert 0 in b.known[0] and 0 not in b.known[1]  # only agent 0 is close enough to see it
    b.exchange(0, [(0, 1), (1, 0)])
    assert 0 in b.known[1] and b.msgs_sent == 2  # one message each way, heard at once with no latency
    lossy = board("greedy", sense_range=5.0, msg_loss=1.0)
    lossy.tasks.append(tasks.Task(id=0, pos=np.array([3.0, 3.0]), size_ticks=3, arrival=0, deadline=None))
    lossy.sense(0, pos)
    lossy.exchange(0, [(0, 1), (1, 0)])
    assert 0 not in lossy.known[1] and lossy.msgs_lost == 2
    late = board("greedy", sense_range=5.0, msg_latency=3)
    late.tasks.append(tasks.Task(id=0, pos=np.array([3.0, 3.0]), size_ticks=3, arrival=0, deadline=None))
    late.sense(0, pos)
    late.exchange(0, [(0, 1), (1, 0)])
    assert 0 not in late.known[1]
    late.exchange(3, [])
    assert 0 in late.known[1]
    oracle = board("oracle", sense_range=5.0)
    oracle.exchange(0, [(0, 1), (1, 0)])
    assert oracle.msgs_sent == 0  # the oracle reads no message, so it sends none


def test_service_misses_and_metrics():
    pos = np.array([[10.0, 10.0], [30.0, 30.0]])
    b = board("oracle", deadline_ticks=5)
    b.tasks.append(tasks.Task(id=0, pos=np.array([10.3, 10.0]), size_ticks=3, arrival=0, deadline=5))
    b.tasks.append(tasks.Task(id=1, pos=np.array([20.0, 5.0]), size_ticks=3, arrival=0, deadline=5))
    vel = np.zeros((2, 2))
    for tick in range(8):
        b.decide(tick, pos)
        b.serve(tick, pos, vel, 0.1)
    m = b.metrics(0.1)
    assert m["tasks_served"] == 1 and m["tasks_missed"] == 1 and m["conflicts"] == 0
    assert m["service_time_mean_s"] == 0.2 and m["served_pct"] == 50.0  # three ticks at the task, from tick 0
    snap = b.snapshot(7)
    assert {t["state"] for t in snap["tasks"]} == {"served", "missed"}


def test_the_sim_runs_the_tasks_mission_and_writes_tasks_jsonl(tmp_path):
    import subprocess
    import sys

    out = tmp_path / "run"
    r = subprocess.run(
        [sys.executable, "simlab/worlds/swarm/sim.py", "--out", str(out), "--agents", "4", "--ticks", "120", "--mission", "tasks",
         "--alloc", "auction", "--task-count", "5", "--task-rate", "0", "--deadline", "0", "--seed", "2"],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
    m = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert m["tasks"]["tasks_total"] == 5 and "tasks_served" in m["headline"] and "msgs_per_served_task" in m["headline"]
    assert m["decisions_by_source"] == {"alloc:auction": 4 * (120 // 5 + (1 if 120 % 5 else 0))} or "alloc:auction" in m["decisions_by_source"]
    rows = [json.loads(l) for l in (out / "tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 120 and len(rows[0]["tasks"]) == 5 and all(t["state"] in {"open", "claimed", "served", "missed"} for t in rows[-1]["tasks"])


def test_message_budget_and_local_lambda2():
    sim = load_script("simlab/worlds/swarm/sim.py", "swarm_sim_s3")
    rnd = random.Random(0)
    full = sim.Comms(4, 100.0, 0.0, 0, rnd)
    edges = [(0, 1), (0, 2), (0, 3), (1, 2)]
    assert len(full.pick_links(edges)) == 8  # every link both ways
    one = sim.Comms(4, 100.0, 0.0, 0, rnd, budget=1)
    sends = one.pick_links(edges)
    assert len(sends) == 4 and all(sum(1 for s, _ in sends if s == a) == 1 for a in range(4))  # one send per agent
    me = np.array([0.0, 0.0])
    assert sim.local_lambda2(me, [], 10.0) == 0.0
    tight = sim.local_lambda2(me, [np.array([3.0, 0.0]), np.array([0.0, 3.0])], 10.0)
    split = sim.local_lambda2(me, [np.array([30.0, 0.0])], 10.0)
    assert tight > 0.5 and split == 0.0


def test_the_lambda2_floor_trades_coverage_for_connectivity(tmp_path):
    import subprocess
    import sys

    got = {}
    for name, floor in (("off", "0"), ("on", "0.8")):
        out = tmp_path / name
        r = subprocess.run(
            [sys.executable, "simlab/worlds/swarm/sim.py", "--out", str(out), "--agents", "8", "--ticks", "150", "--mission", "coverage",
             "--comm-range", "12", "--lambda2-floor", floor, "--seed", "3"],
            capture_output=True, text=True, check=False,
        )
        assert r.returncode == 0, r.stdout[-500:] + r.stderr[-500:]
        got[name] = json.loads((out / "metrics.json").read_text(encoding="utf-8"))["headline"]
    assert got["off"]["connectivity_holds_pct"] == 0 and got["on"]["connectivity_holds_pct"] > 0
    assert got["on"]["mean_lambda2"] > got["off"]["mean_lambda2"]
