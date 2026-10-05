import json

import numpy as np
import pytest
from conftest import load_script

from simlab.lab import child_env


def test_distances_push_out_and_line_of_sight():
    w = load_script("simlab/worlds/swarm/world.py", "swarm_world")
    ob = w.Obstacles(circles=[(10.0, 10.0, 2.0)], boxes=[(20.0, 0.0, 1.0, 10.0)])
    d, out = ob.signed_distance(np.array([13.0, 10.0]))
    assert abs(d - 1.0) < 1e-9 and np.allclose(out, [1, 0])
    d, out = ob.signed_distance(np.array([10.5, 10.0]))
    assert d < 0 and np.allclose(out, [1, 0])
    p, moved = ob.push_out(np.array([20.2, 5.0]), margin=0.1)
    assert moved and p[0] < 20.0
    assert not ob.blocks(np.array([0.0, 10.0]), np.array([5.0, 10.0]))
    assert ob.blocks(np.array([0.0, 10.0]), np.array([15.0, 10.0]))  # through the pillar


def test_named_layouts_and_floor_plans(tmp_path):
    w = load_script("simlab/worlds/swarm/world.py", "swarm_world")
    for name in ("pillars", "rooms", "corridor"):
        assert w.Obstacles.layout_named(name, 40.0), name
    assert not w.Obstacles.layout_named("none", 40.0)
    plan = "# a comment line\n#####\n#...#\n#.D.#\n#...#\n#####\n"
    ob = w.Obstacles.from_plan(plan, 10.0, "plan:t")
    assert ob.layout == "plan:t" and len(ob.boxes) == 4
    assert (0.0, 0.0, 10.0, 2.0) in ob.boxes and (8.0, 2.0, 2.0, 6.0) in ob.boxes
    assert ob.signed_distance(np.array([5.0, 5.0]))[0] > 0 and ob.signed_distance(np.array([1.0, 5.0]))[0] < 0
    names = w.plan_names()
    assert {"office", "warehouse", "maze"} <= set(names)
    for n in names:
        ob = w.Obstacles.layout_named(f"plan:{n}", 40.0)
        assert ob and len(ob.boxes) < 60
        free = sum(
            1
            for x in np.linspace(1, 39, 20)
            for y in np.linspace(1, 39, 20)
            if ob.signed_distance(np.array([x, y]))[0] > 0.5
        )
        assert free > 100, n
    spaced = w.Obstacles.from_plan("# comment\n#  .#\n#...#\n#####\n", 5.0)
    assert len(spaced.boxes) == 3  # a row starting with '# ' is a wall row unless it holds text
    with pytest.raises(ValueError, match="unknown plan"):
        w.Obstacles.layout_named("plan:nope", 40.0)
    with pytest.raises(ValueError, match="unknown layout"):
        w.Obstacles.layout_named("castle", 40.0)
    own = tmp_path / "mine.txt"
    own.write_text("###\n#.#\n###\n", encoding="utf-8")
    assert len(w.Obstacles.layout_named(f"plan:{own}", 3.0).boxes) == 4


def test_an_embedding_project_can_register_a_policy(tmp_path):
    sim = load_script("simlab/worlds/swarm/sim.py", "swarm_sim_ext")
    calls = []

    def always_flock(views, args):
        calls.append((len(views), args.greeting))
        return [("flock", 0.9, "mine") for _ in views]

    def broken(views, args):
        raise RuntimeError("down")

    sim.EXTRA_POLICIES.update({"mine": always_flock, "broken": broken})
    sim.EXTRA_ARGUMENTS.append(lambda ap: ap.add_argument("--greeting", default="hi"))
    common = ["--agents", "4", "--ticks", "30", "--decision-every", "10"]
    assert sim.main(["--out", str(tmp_path / "a"), "--policy", "mine", "--greeting", "yo", *common]) == 0
    assert calls and calls[0] == (4, "yo")
    lines = (tmp_path / "a" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines and all(json.loads(ln)["source"] == "mine" for ln in lines)
    assert sim.main(["--out", str(tmp_path / "b"), "--policy", "broken", *common]) == 0
    lines = (tmp_path / "b" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines and all(json.loads(ln)["source"] == "rules-fallback" for ln in lines)


def test_help_prints_for_both_worlds():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for rel in ("simlab/worlds/swarm/sim.py", "simlab/worlds/vo/synthetic_vo.py"):
        r = subprocess.run(
            [sys.executable, str(root / rel), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env={**child_env(), "PYTHONIOENCODING": "cp1252"},  # the worst case: a legacy Windows console
            check=False,
        )
        assert r.returncode == 0 and "--out" in r.stdout, (rel, r.stderr[-300:])
