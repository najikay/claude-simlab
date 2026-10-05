import json
from pathlib import Path

import pytest

from simlab.lab import plan_problems, validate_experiment

WORLDS = Path(__file__).resolve().parent / "worlds"  # tiny worlds the tests run


def test_shipped_experiments_and_catalogue_agree(lab):
    names = [e["name"] for e in lab.experiments()]
    assert names == ["swarm-coverage", "swarm-formation", "swarm-localisation", "synthetic-vo"]
    cat = lab.catalogue()
    listed = {e for w in cat["worlds"] for e in w["experiments"]}
    assert listed == set(names)
    for e in lab.experiments():
        assert validate_experiment(e) == [], e["name"]
        assert "{run_dir}" in e["command"] and "laya" not in json.dumps(e).lower().replace("layout", "")
    for w in cat["worlds"]:
        for q in w.get("questions", []):
            assert q["experiment"] in names and q["metric"]
            for vname, ov in (q.get("variants") or {}).items():
                exp = lab.experiment(q["experiment"])
                assert set(ov) <= set(exp["params"]), (q["q"], vname)
    assert lab.higher_is_better() >= {"coverage_pct", "connected_pct"}


def test_swarm_run_campaign_and_compare(lab):
    m = lab.run("swarm-formation", {"ticks": 60, "agents": 6, "comm_range": 30})
    assert m["status"] == "done", m["error"]
    h = m["headline"]
    assert h["formation_error_m"] >= 0 and "coverage_pct" in h and m["seconds"] > 0
    d = Path(lab.get(m["run_id"])["dir"])
    assert {
        "manifest.json",
        "metrics.json",
        "report.md",
        "stdout.log",
        "agents.jsonl",
        "trajectory.svg",
    } <= set(lab.get(m["run_id"])["files"])
    assert "swarm-formation" in (d / "report.md").read_text(encoding="utf-8")
    c = lab.campaign("swarm-coverage", ["pillars", "rooms-los"], {"ticks": 40, "agents": 6})
    assert len(c["runs"]) == 2 and c["compare"]["best"]
    rows = lab.runs("swarm-coverage")
    assert [r["variant"] for r in rows] == ["rooms-los", "pillars"]  # newest first
    with pytest.raises(ValueError, match="unknown variant"):
        lab.run("swarm-formation", variant="nope")
    with pytest.raises(ValueError, match="unknown parameter"):
        lab.run("swarm-formation", {"turbo": 1})
    with pytest.raises(ValueError, match="unknown run"):
        lab.get("nope")


def test_vo_run_is_evaluated_and_tune_finds_the_quiet_point(lab):
    m = lab.run("synthetic-vo", {"steps": 120}, variant="gaussian")
    assert m["status"] == "done", m["error"]
    assert m["headline"]["ate_rmse_m"] > 0 and m["headline"]["matched"] > 100
    files = lab.get(m["run_id"])["files"]
    assert {"traj_est.tum", "traj_gt.tum", "trajectory.svg", "metrics.json"} <= set(files)
    t = lab.tune(
        "synthetic-vo",
        "ate_rmse_m",
        {"noise": [0.2, 0.01, 0.05]},
        budget=3,
        params={"steps": 100, "noise_model": "gaussian", "rate": 0},
    )
    assert t["best"]["point"] == {"noise": 0.01} and len(t["trials"]) == 3
    r = lab.tune(
        "synthetic-vo",
        "ate_rmse_m",
        {"noise": [0.2, 0.01]},
        budget=2,
        strategy="random",
        params={"steps": 60, "rate": 0},
    )
    assert {x["point"]["noise"] for x in r["trials"]} == {0.2, 0.01}
    with pytest.raises(ValueError, match="strategy"):
        lab.tune("synthetic-vo", "ate_rmse_m", {"noise": [0.1]}, strategy="magic")


def test_failures_are_recorded_not_raised(lab):
    text = f"name: boom\ncommand: '{{python}} {{script}} {{run_dir}}'\nparams: {{script: {json.dumps(str(WORLDS / 'exit3.py'))}}}\n"
    lab.save_experiment("boom", text)
    m = lab.run("boom")
    assert m["status"] == "error" and "exited 3" in m["error"]
    slow = (
        f"name: slow\ncommand: '{{python}} {{script}} {{run_dir}}'\ntimeout_s: 1\n"
        f"params: {{script: {json.dumps(str(WORLDS / 'sleep5.py'))}}}\n"
    )
    lab.save_experiment("slow", slow)
    assert "timed out" in lab.run("slow")["error"]
    assert validate_experiment({"name": "x", "command": "echo"}) == [
        "the command must write into {run_dir}: include the placeholder"
    ]
    assert "params has no k" in validate_experiment({"name": "x", "command": "a {k} {run_dir}"})[0]
    with pytest.raises(ValueError, match="file name"):
        lab.save_experiment("other", text)


def test_plans_and_own_experiments_live_in_the_home(lab):
    names = [p["name"] for p in lab.plans()]
    assert names == ["maze", "office", "warehouse"] and all(not p["own"] for p in lab.plans())
    p = lab.save_plan("two-rooms", "#######\n#..#..#\n#..D..#\n#######\n")
    assert p["walls"] == 19 and p["doors"] == 1 and p["own"] is True
    assert "two-rooms" in [x["name"] for x in lab.plans()]
    with pytest.raises(ValueError, match="only #"):
        lab.save_plan("x", "###\n.X.\n###\n")
    assert plan_problems("Bad Name", "###\n#.#\n###")[0].startswith("name")
    m = lab.run("swarm-coverage", {"ticks": 30, "agents": 5, "layout": "plan:two-rooms", "comms": "los"})
    assert m["status"] == "done", m["error"]
    assert (
        m["params"]["layout"] == "plan:two-rooms"
        and (Path(lab.get(m["run_id"])["dir"]) / "obstacles.json").exists()
    )


def test_run_ids_stay_ordered_when_the_clock_stalls(lab):
    times = iter([1_700_000_000.0, 1_700_000_000.0, 1_699_999_000.0])
    lab.clock = lambda: next(times)
    a, b, c = (lab._next_stamp() for _ in range(3))
    assert a < b < c


def test_bayes_tuning_runs_and_reports_every_trial(lab):
    t = lab.tune(
        "synthetic-vo",
        "ate_rmse_m",
        {"noise": [0.3, 0.2, 0.1, 0.02, 0.01], "drift": [0.0, 0.01]},
        budget=5,
        strategy="bayes",
        params={"steps": 60, "noise_model": "mixed", "rate": 0},
    )
    assert t["strategy"] == "bayes" and len(t["trials"]) == 5 and t["best"]["value"] is not None
    assert len({json.dumps(x["point"], sort_keys=True) for x in t["trials"]}) == 5, "no repeated points"
    assert all(x["status"] == "done" for x in t["trials"])


def test_run_stamps_are_strictly_increasing_even_when_the_clock_stalls(lab):
    lab.clock = lambda: 1_800_000_000.0  # a frozen clock
    a = lab.run("synthetic-vo", {"steps": 30, "rate": 0}, variant="clean")["run_id"]
    b = lab.run("synthetic-vo", {"steps": 30, "rate": 0}, variant="clean")["run_id"]
    assert a.split("_")[0] < b.split("_")[0] and a != b


def test_parameter_values_cannot_add_a_shell_command(lab):
    marker = lab.home / "pwned"
    script = WORLDS / "echo_word.py"
    # the script path goes through a param (quoted as one word), which is also how a Windows path survives
    lab.save_experiment(
        "echo",
        "name: echo\ncommand: '{python} {script} {run_dir} {word}'\n"
        f"params: {{word: hi, script: {json.dumps(str(script))}}}\n",
    )
    evil = f"x; touch {marker}; $(touch {marker}) | cat"
    m = lab.run("echo", {"word": evil})
    assert m["status"] == "done", m["error"]
    assert not marker.exists(), "a parameter value ran as a second command"
    assert m["headline"] == {"n": len(evil), "argc": 3}  # it arrived as one word
    with pytest.raises(ValueError, match="numbers or strings"):
        lab.run("synthetic-vo", {"steps": [1, 2]})
    with pytest.raises(ValueError, match="outside the catalogue range"):
        lab.run("synthetic-vo", {"steps": 0})
    with pytest.raises(ValueError, match="not one of"):
        lab.run("synthetic-vo", {"shape": "square"})


def test_world_processes_get_only_the_allow_listed_variables(lab, monkeypatch):
    monkeypatch.setenv("SIMLAB_TEST_MARKER_ONE", "1")  # anything not on the allow-list must not arrive
    monkeypatch.setenv("SIMLAB_TEST_MARKER_TWO", "2")
    script = WORLDS / "env_report.py"
    lab.save_experiment(
        "env",
        f"name: env\ncommand: '{{python}} {{script}} {{run_dir}}'\nparams: {{script: {json.dumps(str(script))}}}\n",
    )
    m = lab.run("env")
    assert m["status"] == "done", m["error"]
    assert m["headline"] == {"leaked": 0, "has_path": 1}
