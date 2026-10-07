import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_server(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("simlab_server", ROOT / "servers" / "simlab_server.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.HOME = tmp_path / "home"
    return mod


def rpc(mod, rid, method, params=None):
    return mod.handle({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})


def tool(mod, rid, name, args=None):
    r = rpc(mod, rid, "tools/call", {"name": name, "arguments": args or {}})
    assert "result" in r, r
    return r["result"]["isError"], json.loads(r["result"]["content"][0]["text"]) if r["result"]["content"][0][
        "text"
    ].startswith(("{", "[")) else r["result"]["content"][0]["text"]


def test_server_surface_and_a_full_session(tmp_path, monkeypatch):
    mod = load_server(tmp_path, monkeypatch)
    init = rpc(mod, 1, "initialize", {"protocolVersion": "2025-06-18"})
    assert init["result"]["serverInfo"]["name"] == "sim-lab" and "NOTE" not in init["result"]["instructions"]
    assert mod.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = [t["name"] for t in rpc(mod, 2, "tools/list")["result"]["tools"]]
    assert names == [
        "catalogue",
        "list_experiments",
        "get_experiment",
        "run_experiment",
        "run_campaign",
        "repeat",
        "tune",
        "list_runs",
        "get_run",
        "compare_runs",
        "list_plans",
        "save_plan",
        "save_experiment",
    ]
    err, cat = tool(mod, 3, "catalogue")
    assert not err and [w["id"] for w in cat["worlds"]] == ["swarm", "synthetic-vo"]
    err, one = tool(mod, 4, "catalogue", {"world": "swarm"})
    assert not err and one["id"] == "swarm" and "policy" in one["knobs"]
    err, exps = tool(mod, 5, "list_experiments")
    assert not err and {e["name"] for e in exps} >= {"swarm-formation", "synthetic-vo"}
    err, e = tool(mod, 6, "get_experiment", {"name": "swarm-formation"})
    assert not err and "yaml" in e and "office-los" in e["variants"]
    err, run = tool(
        mod,
        7,
        "run_experiment",
        {"name": "swarm-formation", "params": {"ticks": 40, "agents": 5}, "label": "quick"},
    )
    assert (
        not err
        and run["status"] == "done"
        and run["headline"]["formation_error_m"] >= 0
        and run["report"].startswith("# Run")
    )
    err, run2 = tool(
        mod, 8, "run_experiment", {"name": "synthetic-vo", "variant": "drift", "params": {"steps": 80}}
    )
    assert not err and "ate_rmse_m" in run2["headline"]
    err, cmp_ = tool(mod, 9, "compare_runs", {"run_ids": [run["run_id"], run2["run_id"]]})
    assert not err and len(cmp_["runs"]) == 2
    err, runs = tool(mod, 10, "list_runs", {"limit": 5})
    assert not err and [r["run_id"] for r in runs] == [run2["run_id"], run["run_id"]]
    err, got = tool(mod, 11, "get_run", {"run_id": run["run_id"]})
    assert not err and "manifest.json" in got["files"]
    err, plans = tool(mod, 12, "list_plans")
    assert not err and {p["name"] for p in plans} >= {"office", "maze"}
    err, saved = tool(mod, 13, "save_plan", {"name": "cell", "text": "#####\n#...#\n#.D.#\n#####\n"})
    assert not err and saved["doors"] == 1
    err, bad = tool(mod, 14, "run_experiment", {"name": "nope"})
    assert err and "unknown experiment" in bad
    err, bad2 = tool(mod, 15, "save_experiment", {"name": "x", "yaml": "name: x\ncommand: echo\n"})
    assert err and "run_dir" in bad2
    assert rpc(mod, 16, "tools/call", {"name": "nope"})["error"]["code"] == -32602
    assert mod.handle([1])["error"]["code"] == -32600
    assert rpc(mod, 17, "resources/list")["error"]["code"] == -32601


def test_server_reports_missing_dependencies_instead_of_dying(tmp_path, monkeypatch):
    mod = load_server(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod,
        "IMPORT_ERROR",
        "No module named 'numpy'. Install the two dependencies: python3 -m pip install numpy pyyaml",
    )
    init = rpc(mod, 1, "initialize")
    assert "NOTE: No module named 'numpy'" in init["result"]["instructions"]
    err, text = tool(mod, 2, "list_experiments")
    assert err and "pip install numpy pyyaml" in text
