"""Regression tests for the findings of the pre-submission review."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from simlab.lab import child_env, plan_problems, validate_experiment

ROOT = Path(__file__).resolve().parents[1]


def test_every_catalogue_question_names_real_variants_and_the_vo_one_moves(lab):
    cat = lab.catalogue()
    for w in cat["worlds"]:
        for q in w.get("questions", []):
            exp = lab.experiment(q["experiment"])
            for vname, ov in (q.get("variants") or {}).items():
                assert vname in exp["variants"], (q["q"], vname)
                assert all(exp["variants"][vname].get(k) == v for k, v in ov.items()), (q["q"], vname)
            assert q["metric"] in lab.metrics_of(q["experiment"]), (q["q"], q["metric"])
    vo = next(q for w in cat["worlds"] for q in w.get("questions", []) if q["experiment"] == "synthetic-vo")
    c = lab.campaign("synthetic-vo", list(vo["variants"]), {"steps": 120})
    values = [r["headline"]["ate_rmse_m"] for r in c["compare"]["runs"]]
    assert values == sorted(values) and len(set(values)) == 3, values  # more drift, more error
    # a swarm question runs end to end at a small size
    q = next(q for w in cat["worlds"] for q in w.get("questions", []) if q["experiment"] == "swarm-formation")
    c = lab.campaign("swarm-formation", list(q["variants"])[:2], {"ticks": 20, "agents": 5})
    assert all(r["status"] == "done" for r in c["compare"]["runs"])


def test_shipped_names_are_protected_and_provenance_is_recorded(lab):
    with pytest.raises(ValueError, match="shipped experiment"):
        lab.save_experiment("synthetic-vo", "name: synthetic-vo\ncommand: x {run_dir}\nparams: {}\n")
    with pytest.raises(ValueError, match="shipped plan"):
        lab.save_plan("office", "#####\n#...#\n#####\n")
    p = lab.save_plan("office", "#####\n#...#\n#####\n", overwrite=True)
    assert p["own"] is True
    m = lab.run("swarm-coverage", {"ticks": 20, "agents": 4, "layout": "plan:office"})
    assert m["status"] == "done", m["error"]
    assert m["plan_sha256"] == p["sha256"] and m["own"] is False and m["experiment_sha256"]
    assert m["python_version"].startswith("3.") and m["numpy_version"]
    assert m["experiment_file"].endswith("swarm-coverage.yaml")


def test_tune_checks_the_objective_and_takes_the_direction_from_the_catalogue(lab):
    with pytest.raises(ValueError, match="not a metric"):
        lab.tune("swarm-coverage", "coverage", {"agents": [3, 4]}, params={"ticks": 10})
    with pytest.raises(ValueError, match="budget"):
        lab.tune("synthetic-vo", "ate_rmse_m", {"noise": [0.1]}, budget=0)
    with pytest.raises(ValueError, match="points"):
        lab.tune("synthetic-vo", "ate_rmse_m", {"noise": list(range(80)), "steps": list(range(80))})
    t = lab.tune(
        "swarm-coverage", "coverage_pct", {"agents": [2, 6]}, budget=2, params={"ticks": 40, "comm_range": 30}
    )
    assert t["minimize"] is False  # coverage_pct is higher-is-better
    assert t["best"]["point"] == {"agents": 6}


def test_failures_come_back_with_the_log_and_unknown_runs_are_errors(lab):
    m = lab.run("synthetic-vo", {"steps": 20, "dropout_rate": 0.2, "dropout_len": 100, "rate": 0})
    g = lab.get(m["run_id"])
    assert "stdout_tail" in g
    with pytest.raises(ValueError, match="unknown run"):
        lab.compare([m["run_id"], "20260101-000000-dead_nope"])
    with pytest.raises(ValueError, match="unknown variant"):
        lab.campaign("swarm-formation", ["ideal", "nope"])
    assert len(lab.runs()) == 1, "a refused campaign must not have started a run"
    with pytest.raises(ValueError, match="bad run id"):
        lab.get("x\n")


def test_unreadable_files_in_the_home_are_skipped_not_fatal(lab):
    (lab.home / "experiments").mkdir(parents=True)
    (lab.home / "experiments" / "bad.yaml").write_bytes(b"\xff\xfe name: x")
    (lab.home / "experiments" / "list.yaml").write_text(
        "name: list\ncommand: x {run_dir}\nvariants: [a, b]\n"
    )
    (lab.home / "plans").mkdir()
    (lab.home / "plans" / "bin.txt").write_bytes(b"\xff###\n")
    names = [e["name"] for e in lab.experiments()]
    assert "list" not in names and len(lab.skipped) == 2
    assert "bin" not in [p["name"] for p in lab.plans()]


def test_plan_rules():
    assert plan_problems("u", "###\n#é.\n###") == [
        "only # (wall), . (free), D (door) and space are allowed; found 'é'"
    ]
    assert "no free cell" in plan_problems("u", "###\n###\n###")[0]
    assert plan_problems("bad\n", "###\n#.#\n###")[0].startswith("name")
    assert plan_problems("ok", "# a comment line\n###\n#.#\n###") == []
    assert any(
        m.startswith("placeholder")
        for m in validate_experiment({"name": "x", "command": "a {run_dir.__class__}"})
    )
    assert (
        "variant v: must be a mapping"
        in validate_experiment({"name": "x", "command": "a {run_dir}", "variants": {"v": 5}})[0]
    )


def test_interrupted_runs_are_marked(lab):
    m = lab.run("synthetic-vo", {"steps": 20, "rate": 0}, variant="clean")
    mf = lab.runs_dir / m["run_id"] / "manifest.json"
    d = json.loads(mf.read_text())
    d["status"], d["started"], d["timeout_s"] = "running", "2020-01-01T00:00:00+00:00", 1
    mf.write_text(json.dumps(d))
    row = lab.runs()[0]
    assert row["status"] == "error" and "interrupted" in row["error"]


def test_the_real_stdio_loop(tmp_path):
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": "a", "method": "tools/call", "params": {"name": "get_run", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 3, "params": {}},
        {"jsonrpc": "2.0", "id": {"x": 1}, "method": "ping"},
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "tune",
                "arguments": {
                    "name": "synthetic-vo",
                    "objective": "ate_rmse_m",
                    "space": {"noise": [0.1]},
                    "budget": 99,
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "list_plans", "arguments": {}},
        },
        {"jsonrpc": "2.0", "id": 6, "method": "ping"},
    ]
    r = subprocess.run(
        [sys.executable, str(ROOT / "servers" / "simlab_server.py"), "--home", str(tmp_path)],
        input="\n".join(json.dumps(m) for m in msgs) + "\nnot json\n",
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **child_env(),
            "PYTHONIOENCODING": "utf-8",
        },  # the allow-list keeps SYSTEMROOT, which Windows needs
        check=False,
    )
    lines = [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip()]
    by_id = {str(m.get("id")): m for m in lines}
    assert "1" in by_id, (r.stdout, r.stderr)
    assert by_id["1"]["result"]["protocolVersion"] == "2024-11-05"
    assert "missing argument(s): run_id" in by_id["a"]["result"]["content"][0]["text"]
    assert by_id["3"]["error"]["code"] == -32600
    assert by_id["None"]["error"]["code"] in (-32600, -32700)
    assert "budget" in by_id["4"]["result"]["content"][0]["text"] and by_id["4"]["result"]["isError"]
    assert not by_id["5"]["result"]["isError"] and by_id["6"]["result"] == {}
    assert len(lines) == 8 - 1 + 1, r.stdout  # seven answers to seven requests plus one parse error
    assert r.stderr == "" or "warning" not in r.stderr.lower()
