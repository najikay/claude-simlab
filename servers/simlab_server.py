#!/usr/bin/env python3
"""sim-lab: an MCP server (stdio) over the Sim Lab package next to it.

Tools: list the worlds and experiments, run one (to completion), run a campaign of variants, tune a
parameter space, list and read runs, compare runs, read and save floor plans, save an experiment.
Everything runs on this machine; nothing leaves it. Needs Python 3.10+, numpy and PyYAML.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
INFO = {"name": "sim-lab", "version": "0.1.0"}
INSTRUCTIONS = (
    "A robotics simulation lab. Start with catalogue to learn the worlds, their knobs and example "
    "questions, then run_experiment (seconds for a swarm run) or run_campaign (named variants) and "
    "compare_runs. Each run leaves a folder with manifest.json, metrics.json, report.md and figures. "
    "Pick the metric from the catalogue's higher_is_better list before you call a run 'better'."
)

try:
    import numpy  # noqa: F401
    import yaml  # noqa: F401

    from simlab.lab import Lab, validate_experiment
except ImportError as e:  # the server must still answer, with the fix
    Lab = None  # type: ignore[assignment,misc]
    validate_experiment = None  # type: ignore[assignment]
    IMPORT_ERROR = (
        f"{type(e).__name__}: {e} (Python {sys.version.split()[0]} at {sys.executable}). "
        "The lab needs Python 3.10+ with numpy and pyyaml: python3 -m pip install numpy pyyaml"
    )
else:
    IMPORT_ERROR = ""

_lab: Any = None
HOME: Path | None = None  # set from --home; default ~/.simlab


def lab() -> Any:
    global _lab
    if IMPORT_ERROR:
        raise RuntimeError(IMPORT_ERROR)
    if _lab is None:
        _lab = Lab(home=HOME, python=sys.executable)
    return _lab


def _tool(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    schema: dict = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = required
    return {"name": name, "description": desc, "inputSchema": schema}


PARAMS = {
    "type": "object",
    "description": "parameter overrides, by name (see get_experiment)",
    "additionalProperties": True,
}
TOOLS = [
    _tool(
        "catalogue",
        "The worlds the lab can simulate: what each one is and is not, every knob with unit and range, the metrics and which are better when higher, and example questions with the variants that answer them. Read this first.",
        {"world": {"type": "string", "description": "one world id, else all"}},
    ),
    _tool(
        "list_experiments",
        "Experiments (shipped and the person's own) with their parameters and named variants.",
        {},
    ),
    _tool(
        "get_experiment",
        "One experiment: description, every parameter with its default, the variants, the metrics it writes, and its YAML.",
        {"name": {"type": "string"}},
        ["name"],
    ),
    _tool(
        "run_experiment",
        "Run an experiment to completion with optional parameter overrides or a named variant; returns the run id, status, headline metrics and the report (and the tail of its log when it failed). The call blocks until the run ends: a swarm run of 400 ticks takes a few seconds, 3000 ticks with 60 agents about a minute. Values outside the catalogue's ranges are refused.",
        {
            "name": {"type": "string"},
            "params": PARAMS,
            "variant": {"type": "string"},
            "label": {"type": "string", "description": "a short label stored on the run"},
        },
        ["name"],
    ),
    _tool(
        "run_campaign",
        "Run several named variants of one experiment one after another and return the comparison table. Blocks until all of them end.",
        {
            "name": {"type": "string"},
            "variants": {
                "type": "array",
                "items": {"type": "string"},
                "description": "variant names; default: all of them",
            },
            "params": PARAMS,
            "seeds": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "each variant over every seed; the result then carries mean ± std per metric per variant",
            },
        },
        ["name"],
    ),
    _tool(
        "repeat",
        "Run one configuration over several seeds (default 1..5) and return mean, std, min and max per headline metric: the number to report instead of a single run. Blocks until the runs end.",
        {
            "name": {"type": "string"},
            "variant": {"type": "string"},
            "params": PARAMS,
            "seeds": {"type": "array", "items": {"type": "integer"}},
        },
        ["name"],
    ),
    _tool(
        "tune",
        "Search a parameter space for the best headline metric: strategy grid | random | bayes (Gaussian process + expected improvement). Each trial is a run and the call blocks until the budget is spent, so keep budget small (max 40). The direction comes from the catalogue unless minimize is given.",
        {
            "name": {"type": "string"},
            "objective": {"type": "string", "description": "a headline metric name"},
            "space": {"type": "object", "description": "{param: [values...]}", "additionalProperties": True},
            "minimize": {"type": "boolean"},
            "budget": {"type": "integer", "minimum": 1, "maximum": 40},
            "strategy": {"type": "string", "enum": ["grid", "random", "bayes"]},
            "params": PARAMS,
        },
        ["name", "objective", "space"],
    ),
    _tool(
        "list_runs",
        "Recent runs, newest first, with status and headline metrics.",
        {"experiment": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
    ),
    _tool(
        "get_run",
        "One run in full: manifest (command, params, timings, versions, file hashes), metrics, report, the last lines of its stdout.log (the world's own output and errors) and the files in its folder.",
        {"run_id": {"type": "string"}},
        ["run_id"],
    ),
    _tool(
        "compare_runs",
        "Headline metrics side by side and the best run per metric (lower is better unless the catalogue says otherwise; a tie names no best). Unknown run ids are an error.",
        {"run_ids": {"type": "array", "items": {"type": "string"}, "minItems": 2}},
        ["run_ids"],
    ),
    _tool(
        "list_plans",
        "Floor plans for the swarm worlds (use as layout: plan:<name>): size, walls, doors and the ASCII text.",
        {},
    ),
    _tool(
        "save_plan",
        "Save an ASCII floor plan under the person's lab folder (~/.simlab/plans): # wall, D door, . free, at least 3x3, at least one free cell; it becomes layout plan:<name>. A shipped plan's name is refused unless overwrite is true.",
        {"name": {"type": "string"}, "text": {"type": "string"}, "overwrite": {"type": "boolean"}},
        ["name", "text"],
    ),
    _tool(
        "save_experiment",
        "Save an experiment YAML of the person's own under ~/.simlab/experiments (validated: name, command with {run_dir}, params for every placeholder, variants that only set known params). The command is any local program the person wants the lab to run and score; it runs on their machine with their rights, so only save a command the person asked for. A shipped experiment's name is refused unless overwrite is true (then their file replaces it for every later run).",
        {"name": {"type": "string"}, "yaml": {"type": "string"}, "overwrite": {"type": "boolean"}},
        ["name", "yaml"],
    ),
]


def _brief_run(m: dict, report: str | None = None) -> dict:
    out = {
        k: m.get(k)
        for k in ("run_id", "experiment", "variant", "status", "seconds", "headline", "error", "params")
    }
    out["dir"] = str(lab().runs_dir / m["run_id"])
    if report:
        out["report"] = report
    if m.get("status") != "done":
        out["stdout_tail"] = lab().stdout_tail(m["run_id"])
    return out


def _need(args: dict, *names: str) -> None:
    missing = [n for n in names if n not in args]
    if missing:
        raise ValueError(f"missing argument(s): {', '.join(missing)}")


def _int(args: dict, key: str, default: int, lo: int, hi: int) -> int:
    v = args.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int):
        raise TypeError(f"{key} must be an integer from {lo} to {hi}")
    if not (lo <= v <= hi):
        raise ValueError(f"{key} must be from {lo} to {hi}")
    return v


def _bool(args: dict, key: str, default: bool | None) -> bool | None:
    v = args.get(key, default)
    if v is not None and not isinstance(v, bool):
        raise TypeError(f"{key} must be true or false")
    return v


def call(name: str, args: dict) -> str:
    if name == "catalogue":
        cat = lab().catalogue()
        world = args.get("world")
        if world:
            ws = [w for w in cat.get("worlds", []) if w.get("id") == world]
            if not ws:
                raise ValueError(
                    f"unknown world {world!r}; one of {[w.get('id') for w in cat.get('worlds', [])]}"
                )
            return json.dumps(ws[0], ensure_ascii=False, indent=1)
        return json.dumps(cat, ensure_ascii=False, indent=1)
    if name == "list_experiments":
        rows = [
            {k: e.get(k) for k in ("name", "description", "tags", "params", "metrics", "own")}
            | {"variants": sorted((e.get("variants") or {}).keys())}
            for e in lab().experiments()
        ]
        out: Any = rows
        if lab().skipped:
            out = {"experiments": rows, "skipped": lab().skipped}
        return json.dumps(out, ensure_ascii=False, indent=1)
    if name == "get_experiment":
        _need(args, "name")
        e = lab().experiment(str(args["name"]))
        return json.dumps(
            {
                **{
                    k: e.get(k)
                    for k in (
                        "name",
                        "description",
                        "tags",
                        "params",
                        "variants",
                        "metrics",
                        "outputs",
                        "timeout_s",
                        "own",
                    )
                },
                "yaml": lab().experiment_yaml(e["name"]),
            },
            ensure_ascii=False,
            indent=1,
        )
    if name == "run_experiment":
        _need(args, "name")
        m = lab().run(
            str(args["name"]),
            args.get("params") or {},
            variant=args.get("variant") or None,
            label=args.get("label") or None,
        )
        g = lab().get(m["run_id"])
        return json.dumps(_brief_run(m, g.get("report")), ensure_ascii=False, indent=1)
    if name == "run_campaign":
        _need(args, "name")
        variants = args.get("variants")
        if variants is not None and not (
            isinstance(variants, list) and all(isinstance(v, str) for v in variants)
        ):
            raise ValueError("variants must be a list of names")
        seeds = args.get("seeds")
        if seeds is not None and not (isinstance(seeds, list) and all(isinstance(x, int) for x in seeds)):
            raise ValueError("seeds must be a list of integers")
        c = lab().campaign(str(args["name"]), variants or None, args.get("params") or {}, seeds=seeds or None)
        return json.dumps(c, ensure_ascii=False, indent=1)
    if name == "repeat":
        _need(args, "name")
        seeds = args.get("seeds")
        if seeds is not None and not (isinstance(seeds, list) and all(isinstance(x, int) for x in seeds)):
            raise ValueError("seeds must be a list of integers")
        r = lab().repeat(str(args["name"]), args.get("params") or {}, variant=args.get("variant") or None, seeds=seeds or None)
        return json.dumps(r, ensure_ascii=False, indent=1)
    if name == "tune":
        _need(args, "name", "objective", "space")
        if not isinstance(args["space"], dict):
            raise ValueError("space must be an object {param: [values...]}")
        t = lab().tune(
            str(args["name"]),
            str(args["objective"]),
            dict(args["space"]),
            minimize=_bool(args, "minimize", None),
            budget=_int(args, "budget", 12, 1, 40),
            strategy=str(args.get("strategy") or "grid"),
            params=args.get("params") or {},
        )
        return json.dumps(t, ensure_ascii=False, indent=1)
    if name == "list_runs":
        return json.dumps(
            lab().runs(args.get("experiment") or None, _int(args, "limit", 30, 1, 200)),
            ensure_ascii=False,
            indent=1,
        )
    if name == "get_run":
        _need(args, "run_id")
        return json.dumps(lab().get(str(args["run_id"])), ensure_ascii=False, indent=1)
    if name == "compare_runs":
        _need(args, "run_ids")
        ids = args["run_ids"]
        if not isinstance(ids, list) or len(ids) < 2:
            raise ValueError("run_ids must be a list of at least two run ids")
        return json.dumps(lab().compare([str(x) for x in ids]), ensure_ascii=False, indent=1)
    if name == "list_plans":
        return json.dumps(lab().plans(), ensure_ascii=False, indent=1)
    if name == "save_plan":
        _need(args, "name", "text")
        p = lab().save_plan(
            str(args["name"]), str(args["text"]), overwrite=bool(_bool(args, "overwrite", False))
        )
        return json.dumps(p, ensure_ascii=False, indent=1)
    if name == "save_experiment":
        _need(args, "name", "yaml")
        e = lab().save_experiment(
            str(args["name"]), str(args["yaml"]), overwrite=bool(_bool(args, "overwrite", False))
        )
        return json.dumps(
            {k: e.get(k) for k in ("name", "description", "params", "variants", "file")},
            ensure_ascii=False,
            indent=1,
        )
    raise ValueError(f"unknown tool: {name}")


def handle(msg: Any) -> dict | None:
    """One JSON-RPC message → a response, or None for a notification. Never raises."""
    if not isinstance(msg, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "a request must be a JSON object"},
        }
    rid, method, params = msg.get("id"), msg.get("method"), msg.get("params")
    if params is None:
        params = {}
    if "id" not in msg:  # a notification: no answer, whatever it was
        return None
    if not isinstance(rid, (str, int)) or isinstance(rid, bool):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "id must be a string or a number"},
        }
    if not isinstance(method, str) or not method:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32600, "message": "a request needs a method"}}
    if not isinstance(params, dict):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "params must be an object"}}
    if method == "initialize":
        asked = str(params.get("protocolVersion") or PROTOCOL_VERSION)
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": asked if asked in SUPPORTED_VERSIONS else PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": INFO,
                "instructions": INSTRUCTIONS + (f" NOTE: {IMPORT_ERROR}" if IMPORT_ERROR else ""),
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = str(params.get("name", ""))
        if name not in {t["name"] for t in TOOLS}:
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32602, "message": f"unknown tool: {name}"},
            }
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32602, "message": "arguments must be an object"},
            }
        try:
            text = call(name, dict(arguments))
        except Exception as e:  # noqa: BLE001 - a tool error is an answer, not a crash
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                    "isError": True,
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"content": [{"type": "text", "text": text}], "isError": False},
        }
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def main() -> None:
    global HOME
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--home":
        HOME = Path(args[1])
    elif args:
        sys.stderr.write("usage: simlab_server.py [--home DIR]\n")
        sys.exit(2)
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            sys.stdout.write(
                json.dumps(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
                )
                + "\n"
            )
            sys.stdout.flush()
            continue
        try:
            out = handle(msg)
        except Exception as e:  # noqa: BLE001
            out = {
                "jsonrpc": "2.0",
                "id": msg.get("id") if isinstance(msg, dict) else None,
                "error": {"code": -32603, "message": f"internal error: {type(e).__name__}"},
            }
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=True) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
