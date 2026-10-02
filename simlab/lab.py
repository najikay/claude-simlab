"""The lab: experiments as YAML, runs as folders, metrics as JSON, compare and tune on top.

A run is reproducible from its folder alone: ``manifest.json`` (the exact command, every parameter,
the seed, the lab, Python and NumPy versions, a hash of the experiment file and of the floor plan, timings), ``stdout.log``, the outputs the command wrote,
``metrics.json`` (a ``headline`` object of numbers) and ``report.md``. Nothing here needs a server;
the MCP server in ``servers/`` is a thin wrapper over this module.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import platform
import random
import re
import shlex
import string
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from simlab.evaluate import evaluate, trajectory_svg

UTC = timezone.utc  # datetime.UTC needs 3.11; the lab supports 3.10

ROOT = Path(__file__).resolve().parent  # the simlab package: experiments/, worlds/, plans/, catalogue.yaml
BOOKKEEPING = {"matched", "ticks", "agents", "steps", "seconds", "messages", "decisions"}
PLAN_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,30}")
EXPERIMENT_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
RUN_ID = re.compile(r"[A-Za-z0-9_-]+")
PLAN_CHARS = set("#.D ")
MAX_GRID = 5000  # tune refuses a larger Cartesian product
MAX_BUDGET = 40
STDOUT_TAIL = 40  # lines of stdout.log returned with a run


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def default_home() -> Path:
    """Where runs live: ``$SIMLAB_HOME`` or ``~/.simlab``."""
    return Path(os.environ.get("SIMLAB_HOME") or Path.home() / ".simlab")


class Lab:
    """Experiments, runs, campaigns, tuning, comparison, the catalogue and floor plans."""

    def __init__(self, home: Path | None = None, python: str | None = None) -> None:
        """``home`` holds ``runs/`` and the person's own ``experiments/`` and ``plans/`` (optional)."""
        self.home = Path(home or default_home())
        self.runs_dir = self.home / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.python = python or sys.executable
        self._last_stamp = ""
        self.clock = time.time

    # -- experiments -----------------------------------------------------------------------------
    def experiment_dirs(self) -> list[Path]:
        own = self.home / "experiments"
        return [ROOT / "experiments"] + ([own] if own.is_dir() else [])

    def experiments(self) -> list[dict]:
        """Every experiment, shipped ones first, the person's own after (same name: theirs wins).

        Files that cannot be read or do not validate are skipped and listed in ``self.skipped``.
        """
        found: dict[str, dict] = {}
        self.skipped: list[dict] = []
        for d in self.experiment_dirs():
            for f in sorted(d.glob("*.yaml")):
                try:
                    text = f.read_text(encoding="utf-8")
                    data = yaml.safe_load(text) or {}
                    problems = validate_experiment(data)
                except (yaml.YAMLError, UnicodeDecodeError, OSError) as e:
                    problems = [f"{type(e).__name__}: {str(e)[:120]}"]
                    data = {}
                if problems:
                    self.skipped.append({"file": str(f), "problems": problems})
                    continue
                data["file"] = str(f)
                data["own"] = d != ROOT / "experiments"
                data["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
                found[str(data["name"])] = data
        return [found[k] for k in sorted(found)]

    def shipped_names(self) -> set[str]:
        return {f.stem for f in (ROOT / "experiments").glob("*.yaml")}

    def experiment(self, name: str) -> dict:
        for e in self.experiments():
            if e["name"] == name:
                return e
        raise ValueError(f"unknown experiment {name!r}; one of {[e['name'] for e in self.experiments()]}")

    def experiment_yaml(self, name: str) -> str:
        return Path(self.experiment(name)["file"]).read_text(encoding="utf-8")

    def save_experiment(self, name: str, text: str, overwrite: bool = False) -> dict:
        """Write an experiment of the person's own (under ``home/experiments``); validated first.

        A shipped name is refused unless ``overwrite`` is set: the person's file would silently replace
        the experiment every later run by that name uses.
        """
        if not EXPERIMENT_NAME.fullmatch(name):
            raise ValueError("name: lowercase letters, digits and dashes")
        if name in self.shipped_names() and not overwrite:
            raise ValueError(f"{name!r} is a shipped experiment; pick another name or pass overwrite")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ValueError(f"not valid YAML: {str(e)[:200]}") from e
        problems = validate_experiment(data)
        if problems:
            raise ValueError("; ".join(problems))
        if data.get("name") != name:
            raise ValueError(f"the YAML's name is {data.get('name')!r}, the file name is {name!r}")
        d = self.home / "experiments"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.yaml").write_text(text, encoding="utf-8")
        return self.experiment(name)

    # -- runs ------------------------------------------------------------------------------------
    def _next_stamp(self) -> str:
        """Strictly increasing run stamps even when the wall clock stalls or steps back."""
        stamp = datetime.fromtimestamp(self.clock(), tz=UTC).strftime("%Y%m%d-%H%M%S")
        if stamp <= self._last_stamp:
            last = datetime.strptime(self._last_stamp, "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
            stamp = (last + timedelta(seconds=1)).strftime("%Y%m%d-%H%M%S")
        self._last_stamp = stamp
        return stamp

    def run(
        self,
        name: str,
        params: dict | None = None,
        variant: str | None = None,
        campaign: str | None = None,
        label: str | None = None,
        timeout_s: int | None = None,
    ) -> dict:
        """Run an experiment to completion and return its manifest (with ``headline`` metrics)."""
        exp = self.experiment(name)
        if not isinstance(params or {}, dict):
            raise TypeError("params must be a mapping of name → value")
        if variant:
            variants = exp.get("variants") or {}
            if variant not in variants:
                raise ValueError(f"unknown variant {variant!r}; one of {sorted(variants)}")
            params = {**(variants[variant] or {}), **(params or {})}
        merged = {
            **(exp.get("params") or {}),
            **{k: v for k, v in (params or {}).items() if v is not None and v != ""},
        }
        unknown = sorted(k for k in merged if k not in (exp.get("params") or {}))
        if unknown:
            raise ValueError(f"unknown parameter(s) for {name}: {unknown}")
        bad = [k for k, v in merged.items() if not isinstance(v, (int, float, str)) or isinstance(v, bool)]
        if bad:
            raise ValueError(f"parameter values must be numbers or strings: {bad}")
        out_of_range = self.range_problems(name, merged)
        if out_of_range:
            raise ValueError("; ".join(out_of_range))
        run_id = re.sub(r"[^A-Za-z0-9_-]+", "-", f"{self._next_stamp()}-{uuid.uuid4().hex[:4]}_{name}")
        run_dir = self.runs_dir / run_id
        raw = {"python": self.python, "repo": str(ROOT.parent), "run_dir": str(run_dir)}
        # every value is quoted as one shell word and the command runs without a shell: a parameter
        # can never add a second command, however it was typed
        subs = {k: shlex.quote(v) for k, v in raw.items()} | {
            k: shlex.quote(str(v)) for k, v in merged.items()
        }
        try:
            command = str(exp["command"]).format(**subs)
            cwd = str(exp.get("cwd") or "{repo}").format(**raw, **{k: str(v) for k, v in merged.items()})
            argv = shlex.split(command)
        except (KeyError, ValueError) as e:
            raise ValueError(f"the command needs a parameter that is not set, or is malformed: {e}") from e
        if not argv:
            raise ValueError("the command is empty")
        run_dir.mkdir(parents=True, exist_ok=False)
        m = {
            "run_id": run_id,
            "experiment": name,
            "description": exp.get("description"),
            "experiment_file": exp.get("file"),
            "experiment_sha256": exp.get("sha256"),
            "own": bool(exp.get("own")),
            "plan_sha256": self._plan_hash(merged),
            "params": merged,
            "variant": variant or label,
            "campaign": campaign,
            "command": command,
            "cwd": cwd,
            "timeout_s": int(timeout_s or exp.get("timeout_s") or 600),
            "outputs": exp.get("outputs") or {},
            "eval": exp.get("eval") or {},
            "lab_version": _lab_version(),
            "python_version": platform.python_version(),
            "numpy_version": _numpy_version(),
            "started": _now(),
            "finished": None,
            "status": "running",
            "returncode": None,
            "seconds": None,
            "error": None,
        }
        _write_json(run_dir / "manifest.json", m)
        t0 = time.time()
        try:
            env = {**os.environ, "SIMLAB_PLANS": str(self.home / "plans"), "PYTHONIOENCODING": "utf-8"}
            with (run_dir / "stdout.log").open("w", encoding="utf-8") as log:
                r = subprocess.run(
                    argv,
                    cwd=cwd,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=m["timeout_s"],
                    check=False,
                )
            m["returncode"] = r.returncode
            if r.returncode != 0:
                m["status"], m["error"] = "error", f"command exited {r.returncode}; see stdout.log"
            else:
                self._evaluate(m, run_dir)
                if not m.get("headline"):
                    raise FileNotFoundError("the command exited 0 but wrote no metrics.json with a headline")
                m["status"] = "done"
        except subprocess.TimeoutExpired:
            m["status"], m["error"] = "error", f"timed out after {m['timeout_s']} s"
        except Exception as e:  # noqa: BLE001 - recorded on the run, never raised past it
            m["status"], m["error"] = "error", f"{type(e).__name__}: {str(e)[:300]}"
        m["seconds"] = round(time.time() - t0, 1)
        m["finished"] = _now()
        try:
            self._report(m, run_dir)
        except Exception as e:  # noqa: BLE001
            m["report_error"] = str(e)[:300]
        _write_json(run_dir / "manifest.json", m)
        return m

    def world_of(self, experiment: str) -> dict | None:
        """The catalogue entry whose ``experiments`` list names this experiment, if any."""
        for w in self.catalogue().get("worlds") or []:
            if experiment in (w.get("experiments") or []):
                return w
        return None

    def range_problems(self, experiment: str, params: dict) -> list[str]:
        """Parameter values outside the catalogue's range or value list for that world (documentation-backed)."""
        w = self.world_of(experiment)
        if not w:
            return []
        out = []
        for k, v in params.items():
            knob = (w.get("knobs") or {}).get(k)
            if not isinstance(knob, dict):
                continue
            rng, values = knob.get("range"), knob.get("values")
            if isinstance(rng, list) and len(rng) == 2 and isinstance(v, (int, float)):
                lo, hi = rng
                if not (lo <= v <= hi):
                    out.append(f"{k}={v} is outside the catalogue range {lo}..{hi} ({knob.get('unit', '')})")
            elif isinstance(values, list) and values and str(v) not in [str(x) for x in values]:
                if str(v).startswith("plan:") and any(str(x).startswith("plan:") for x in values):
                    continue  # any floor plan, shipped or the person's own
                out.append(f"{k}={v!r} is not one of {values}")
        return out

    def _plan_hash(self, params: dict) -> str | None:
        layout = str(params.get("layout") or "")
        if not layout.startswith("plan:"):
            return None
        for d in reversed(self.plan_dirs()):  # the person's own plan wins, like the simulator
            f = d / f"{layout[5:]}.txt"
            if f.is_file():
                try:
                    return hashlib.sha256(f.read_bytes()).hexdigest()[:16]
                except OSError:
                    return None
        return None

    def _evaluate(self, m: dict, run_dir: Path) -> None:
        outs = m.get("outputs") or {}
        est, gt = outs.get("est"), outs.get("gt")
        if not (est and gt):  # the command scored itself: take its headline
            own = run_dir / "metrics.json"
            if own.exists():
                data = _read_json(own)
                head = data.get("headline") if isinstance(data, dict) else None
                if isinstance(head, dict):
                    m["headline"] = {
                        k: v for k, v in head.items() if isinstance(v, (int, float)) or v is None
                    }
            return
        pe, pg = run_dir / est, run_dir / gt
        if not (pe.exists() and pg.exists()):
            raise FileNotFoundError(f"expected output missing: {est if not pe.exists() else gt}")
        metrics = evaluate(pe, pg, max_diff=float((m.get("eval") or {}).get("max_diff") or 0.02))
        _write_json(run_dir / "metrics.json", metrics)
        (run_dir / "trajectory.svg").write_text(trajectory_svg(pe, pg), encoding="utf-8")
        m["headline"] = {
            "ate_rmse_m": round(metrics["ate"]["rmse"], 4),
            "rpe1_rmse_m": round(metrics["rpe_1"]["rmse"], 4),
            "drift_pct": round(metrics["drift_pct"], 3) if metrics.get("drift_pct") is not None else None,
            "matched": metrics["matched"],
        }

    def _report(self, m: dict, run_dir: Path) -> None:
        met = _read_json(run_dir / "metrics.json") if (run_dir / "metrics.json").exists() else None
        lines = [
            f"# Run {m['run_id']}",
            "",
            f"Experiment **{m['experiment']}**"
            + (f" · variant **{m['variant']}**" if m.get("variant") else "")
            + f" — {m.get('description') or ''}",
            "",
            f"- status: **{m['status']}**{(' · ' + m['error']) if m.get('error') else ''}",
            f"- command: `{m['command']}`",
            "- params: " + ", ".join(f"{k}={v}" for k, v in (m.get("params") or {}).items()),
            f"- lab {m.get('lab_version')} · {m.get('seconds')} s · started {m['started']}",
            "",
        ]
        if met and "ate" in met:
            a, r1 = met["ate"], met["rpe_1"]
            lines += [
                "## Metrics",
                "",
                "| metric | value |",
                "|---|---|",
                f"| ATE RMSE (m) | {a['rmse']:.4f} |",
                f"| ATE mean / median / max (m) | {a['mean']:.4f} / {a['median']:.4f} / {a['max']:.4f} |",
                f"| RPE Δ1 RMSE (m) | {r1['rmse']:.4f} |",
                (
                    f"| RPE Δ10 RMSE (m) | {met['rpe_10']['rmse']:.4f} |"
                    if met.get("rpe_10")
                    else "| RPE Δ10 | n/a |"
                ),
                (
                    f"| drift (% of {met['gt_length_m']:.1f} m) | {met['drift_pct']:.3f} |"
                    if met.get("drift_pct") is not None
                    else "| drift | n/a |"
                ),
                f"| matched poses | {met['matched']} of {met['est_poses']} est / {met['gt_poses']} gt |",
                "",
                "![trajectory](trajectory.svg)",
                "",
            ]
        elif met and isinstance(met.get("headline"), dict):
            lines += ["## Metrics (from the simulator)", "", "| metric | value |", "|---|---|"]
            lines += [f"| {k} | {v} |" for k, v in met["headline"].items()]
            lines.append("")
            for fig in ("trajectory.svg", "metrics.svg"):
                if (run_dir / fig).exists():
                    lines += [f"![{fig}]({fig})", ""]
        (run_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def runs(self, experiment: str | None = None, limit: int = 50) -> list[dict]:
        """Run summaries, newest first."""
        out = []
        for d in sorted(self.runs_dir.iterdir(), reverse=True) if self.runs_dir.is_dir() else []:
            mf = d / "manifest.json"
            if not mf.is_file():
                continue
            try:
                m = _read_json(mf)
            except ValueError:
                continue
            if experiment and m.get("experiment") != experiment:
                continue
            if m.get("status") == "running" and self._stale(m):
                m["status"], m["error"] = (
                    "error",
                    "interrupted: the lab process ended before the run finished",
                )
                m["finished"] = _now()
                _write_json(mf, m)
            out.append(
                {
                    k: m.get(k)
                    for k in (
                        "run_id",
                        "experiment",
                        "variant",
                        "campaign",
                        "status",
                        "started",
                        "seconds",
                        "headline",
                        "params",
                        "error",
                    )
                }
            )
            if len(out) >= limit:
                break
        return out

    def _stale(self, m: dict) -> bool:
        try:
            started = datetime.fromisoformat(str(m.get("started")))
        except ValueError:
            return True
        grace = int(m.get("timeout_s") or 600) + 60
        return (datetime.now(UTC) - started).total_seconds() > grace

    def stdout_tail(self, run_id: str, lines: int = STDOUT_TAIL) -> str:
        """The last lines of a run's ``stdout.log`` (what the world printed, including its error)."""
        f = self.runs_dir / run_id / "stdout.log"
        if not f.is_file():
            return ""
        return "\n".join(f.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])

    def get(self, run_id: str) -> dict:
        """Manifest, metrics, report, the tail of stdout and the files of one run."""
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("bad run id")
        d = self.runs_dir / run_id
        if not (d / "manifest.json").is_file():
            raise ValueError(f"unknown run {run_id!r}")
        out = {
            "manifest": _read_json(d / "manifest.json"),
            "dir": str(d),
            "stdout_tail": self.stdout_tail(run_id),
        }
        if (d / "metrics.json").exists():
            out["metrics"] = _read_json(d / "metrics.json")
        if (d / "report.md").exists():
            out["report"] = (d / "report.md").read_text(encoding="utf-8")
        out["files"] = sorted(p.name for p in d.iterdir() if p.is_file())
        return out

    # -- campaigns, compare, tune ----------------------------------------------------------------
    def campaign(self, name: str, variants: list[str] | None = None, params: dict | None = None) -> dict:
        """Run several variants one after another under one campaign id."""
        exp = self.experiment(name)
        known = exp.get("variants") or {}
        chosen = list(variants or sorted(known))
        if not chosen:
            raise ValueError(f"{name} has no variants; pass params to run() instead")
        unknown = [v for v in chosen if v not in known]
        if unknown:
            raise ValueError(f"unknown variant(s) {unknown}; one of {sorted(known)}")
        cid = f"c-{uuid.uuid4().hex[:6]}"
        runs = [self.run(name, params, variant=v, campaign=cid) for v in chosen]
        return {
            "id": cid,
            "experiment": name,
            "variants": chosen,
            "runs": [r["run_id"] for r in runs],
            "compare": self.compare([r["run_id"] for r in runs]),
        }

    def metrics_of(self, experiment: str) -> set[str]:
        """Headline metric names an experiment can produce: its YAML list plus its world's catalogue metrics."""
        exp = self.experiment(experiment)
        out = {str(m) for m in (exp.get("metrics") or [])}
        w = self.world_of(experiment)
        if w:
            out |= set((w.get("metrics") or {}).keys())
        return out

    def higher_is_better(self) -> set[str]:
        out: set[str] = set()
        for w in self.catalogue().get("worlds") or []:
            out |= set(w.get("higher_is_better") or [])
        return out

    def compare(self, run_ids: list[str]) -> dict:
        """Headline metrics per run plus the best run per metric (the catalogue says which way is better)."""
        rows = []
        for rid in run_ids:
            g = self.get(rid)  # unknown ids raise: a silent empty table would read as "no difference"
            m = g["manifest"]
            rows.append(
                {k: m.get(k) for k in ("run_id", "experiment", "variant", "params", "headline", "status")}
            )
        higher = self.higher_is_better()
        keys: list[str] = []
        for r in rows:
            for k, v in (r.get("headline") or {}).items():
                if k in BOOKKEEPING or isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                if k not in keys:
                    keys.append(k)
        best: dict[str, str] = {}
        for key in keys:
            cands = [
                (r["headline"][key], r["run_id"])
                for r in rows
                if r.get("headline") and isinstance(r["headline"].get(key), (int, float))
            ]
            if len(cands) >= 2:
                top = (max(cands) if key in higher else min(cands))[0]
                winners = [rid for v, rid in cands if v == top]
                if len(winners) == 1:  # a tie has no best run
                    best[key] = winners[0]
        return {"runs": rows, "best": best, "higher_is_better": sorted(higher)}

    def tune(
        self,
        name: str,
        objective: str,
        space: dict[str, list],
        minimize: bool | None = None,
        budget: int = 12,
        strategy: str = "grid",
        params: dict | None = None,
        seed: int = 0,
    ) -> dict:
        """Search a parameter space for the best headline metric: grid, random or bayes (GP + expected improvement).

        ``minimize`` defaults to the catalogue's direction for the objective (higher_is_better → maximise).
        """
        if (
            not isinstance(space, dict)
            or not space
            or any(not isinstance(v, list) or not v for v in space.values())
        ):
            raise ValueError("space: {param: [values...]} with at least one value each")
        keys = sorted(space)
        exp = self.experiment(name)
        unknown = [k for k in keys if k not in (exp.get("params") or {})]
        if unknown:
            raise ValueError(f"unknown parameter(s): {unknown}")
        known_metrics = self.metrics_of(name)
        if known_metrics and objective not in known_metrics:
            raise ValueError(
                f"objective {objective!r} is not a metric of {name}; one of {sorted(known_metrics)}"
            )
        if minimize is None:
            minimize = objective not in self.higher_is_better()
        if not isinstance(budget, int) or isinstance(budget, bool) or not (1 <= budget <= MAX_BUDGET):
            raise ValueError(f"budget: an integer from 1 to {MAX_BUDGET}")
        size = 1
        for k in keys:
            size *= len(space[k])
        if size > MAX_GRID:
            raise ValueError(f"the space has {size} points; keep it under {MAX_GRID} (fewer values per knob)")
        grid = [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*(space[k] for k in keys))]
        if strategy == "grid":
            points = grid[:budget]
        elif strategy == "random":
            rnd = random.Random(seed)
            points = rnd.sample(grid, min(budget, len(grid)))
        elif strategy == "bayes":
            from simlab.bayes import BayesProposer

            points = []  # chosen as we go
        else:
            raise ValueError("strategy: grid | random | bayes")
        tid = f"t-{uuid.uuid4().hex[:6]}"
        trials: list[dict] = []
        proposer = (
            BayesProposer(grid, budget=min(budget, len(grid)), minimize=minimize, seed=seed)
            if strategy == "bayes"
            else None
        )
        for i in range(min(budget, len(grid))):
            point = proposer.next() if proposer else points[i]
            if point is None:
                break
            label = ", ".join(f"{k}={point[k]}" for k in keys)
            m = self.run(name, {**(params or {}), **point}, campaign=tid, label=label)
            value = (m.get("headline") or {}).get(objective)
            if proposer:
                proposer.tell(point, value if isinstance(value, (int, float)) else None)
            trials.append(
                {
                    "point": point,
                    "run_id": m["run_id"],
                    "value": value if isinstance(value, (int, float)) else None,
                    "status": m["status"],
                }
            )
        scored = [t for t in trials if t["value"] is not None]
        best = (min if minimize else max)(scored, key=lambda t: t["value"]) if scored else None
        return {
            "id": tid,
            "experiment": name,
            "objective": objective,
            "minimize": minimize,
            "strategy": strategy,
            "trials": trials,
            "best": best,
        }

    # -- catalogue and plans ---------------------------------------------------------------------
    def catalogue(self) -> dict:
        """The worlds: what each simulates and does not, every knob, the metrics, example questions."""
        return yaml.safe_load((ROOT / "catalogue.yaml").read_text(encoding="utf-8")) or {}

    def plan_dirs(self) -> list[Path]:
        own = self.home / "plans"
        return [ROOT / "plans"] + ([own] if own.is_dir() else [])

    def plans(self) -> list[dict]:
        out: dict[str, dict] = {}
        for d in self.plan_dirs():
            for f in sorted(d.glob("*.txt")):
                try:
                    text = f.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                rows = plan_grid(text)
                out[f.stem] = {
                    "name": f.stem,
                    "layout": f"plan:{f.stem}",
                    "rows": len(rows),
                    "cols": len(rows[0]) if rows else 0,
                    "walls": sum(r.count("#") for r in rows),
                    "doors": sum(r.count("D") for r in rows),
                    "own": d != ROOT / "plans",
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                    "text": text,
                }
        return [out[k] for k in sorted(out)]

    def save_plan(self, name: str, text: str, overwrite: bool = False) -> dict:
        """Write a plan of the person's own; a shipped name is refused unless ``overwrite`` is set."""
        problems = plan_problems(name, text)
        if problems:
            raise ValueError("; ".join(problems))
        if (ROOT / "plans" / f"{name}.txt").is_file() and not overwrite:
            raise ValueError(f"{name!r} is a shipped plan; pick another name or pass overwrite")
        d = self.home / "plans"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.txt").write_text(text.rstrip("\n") + "\n", encoding="utf-8")
        return next(p for p in self.plans() if p["name"] == name)


# -- helpers ------------------------------------------------------------------------------------------
def _lab_version() -> str:
    from simlab import __version__

    return __version__


def _numpy_version() -> str | None:
    try:
        import numpy

        return str(numpy.__version__)
    except ImportError:
        return None


def command_fields(cmd: str) -> list[str]:
    """Placeholder names of a command template, or raise ValueError when the template is malformed."""
    out = []
    for _, field, spec, conv in string.Formatter().parse(cmd):
        if field is None:
            continue
        if not re.fullmatch(r"\w+", field) or spec or conv:
            raise ValueError(f"placeholder {{{field}}}: plain names only, no attributes, indexes or formats")
        out.append(field)
    return out


def validate_experiment(data: Any) -> list[str]:
    """Why an experiment YAML cannot run (empty list = fine)."""
    if not isinstance(data, dict):
        return ["the YAML must be a mapping"]
    out = []
    for key in ("name", "command"):
        if not data.get(key):
            out.append(f"missing {key}")
    cmd = str(data.get("command") or "")
    if cmd and "{run_dir}" not in cmd:
        out.append("the command must write into {run_dir}: include the placeholder")
    params = data.get("params") or {}
    if not isinstance(params, dict):
        out.append("params must be a mapping")
        params = {}
    try:
        fields = command_fields(cmd)
    except ValueError as e:
        fields = []
        out.append(str(e))
    for ph in fields:
        if ph not in ("python", "repo", "run_dir") and ph not in params:
            out.append(f"the command uses {{{ph}}} but params has no {ph}")
    for k, v in params.items():
        if not isinstance(v, (int, float, str)) or isinstance(v, bool):
            out.append(f"param {k}: values must be numbers or strings")
    variants = data.get("variants") or {}
    if not isinstance(variants, dict):
        out.append("variants must be a mapping of name → params")
    else:
        for vname, ov in variants.items():
            if ov is not None and not isinstance(ov, dict):
                out.append(f"variant {vname}: must be a mapping of param → value")
                continue
            for k in ov or {}:
                if k not in params:
                    out.append(f"variant {vname}: {k} is not a param")
    outs = data.get("outputs") or {}
    if outs and not ({"est", "gt"} <= set(outs)):
        out.append("outputs must name both est and gt (TUM files), or be empty for a self-scored command")
    return out


def is_plan_comment(line: str) -> bool:
    """A comment is ``# `` followed by text that is not plan characters (``# office: four rooms``)."""
    return line.startswith("# ") and any(c not in PLAN_CHARS for c in line)


def plan_grid(text: str) -> list[str]:
    """Rows of an ASCII floor plan: comment and blank lines dropped, rows padded to one width."""
    rows = [ln.rstrip("\n") for ln in text.splitlines() if ln.strip() and not is_plan_comment(ln)]
    width = max((len(r) for r in rows), default=0)
    return [r.ljust(width, ".") for r in rows]


def plan_problems(name: str, text: str) -> list[str]:
    out = []
    if not PLAN_NAME.fullmatch(name):
        out.append("name: lowercase letters, digits and dashes, 1-31 characters")
    rows = plan_grid(text)
    if len(rows) < 3 or (rows and len(rows[0]) < 3):
        out.append("a plan needs at least 3 rows and 3 columns")
    bad = sorted({c for r in rows for c in r if c not in PLAN_CHARS})
    if bad:
        out.append(f"only # (wall), . (free), D (door) and space are allowed; found {''.join(bad)!r}")
    if rows and not any("#" in r for r in rows):
        out.append("no walls at all: use layout none instead")
    if rows and not any(c in ".D " for r in rows for c in r):
        out.append("no free cell at all: the agents need somewhere to be")
    return out
