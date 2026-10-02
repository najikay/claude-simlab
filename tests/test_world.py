import numpy as np
import pytest
from conftest import load_script


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
