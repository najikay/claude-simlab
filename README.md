<p align="center"><img src="assets/banner.png" alt="Sim Lab" width="820"></p>

# Sim Lab

**A robotics simulation lab for Claude.** Ask a question about a swarm, a formation, coverage, cooperative localisation or visual odometry, and Claude designs the experiment, runs it on your machine, compares the runs and answers with numbers you can reproduce.

> "Does a formation of 12 drones still close when the radio drops 60 % of messages?"
> → one campaign, four runs, a compare table, an answer in metres, and a findings note with the run ids.

Everything runs locally. No account, no network, no telemetry. Python 3.10+, `numpy` and `pyyaml`.

<p align="center">
<img src="docs/figures/swarm-office.png" alt="8 agents covering the office floor plan with line-of-sight radio" width="46%">
<img src="docs/figures/vo-drift.png" alt="synthetic visual odometry: truth vs estimate under heading drift" width="46%">
</p>
<p align="center"><sub>Left: <code>swarm-coverage</code> on the <code>plan:office</code> layout with walls blocking radio links. Right: <code>synthetic-vo</code> under heading drift, truth (grey) vs estimate (blue). Both drawn by <code>docs/figures/make_figures.py</code> from real runs.</sub></p>

## What is in the box

| Piece | What it does |
|---|---|
| **`sim-lab` MCP server** | 12 tools: catalogue, experiments, run, campaign, tune, runs, compare, floor plans. Stdio, stdlib JSON-RPC, no framework. |
| **`simlab-run` skill** | How Claude turns a question into a one-knob sweep, estimates cost, runs, compares, checks the seed and answers. |
| **`simlab-findings` skill** | A one-page reproducible findings note: question, setup, table, reading, limits, reproduce. |
| **Swarm world** | N agents in a 2-D arena with range-limited lossy, delayed radio; behaviours flock, formation, rendezvous, coverage, goto; belief filters (exponential, Kalman, unicycle EKF with range-bearing fusion and covariance intersection); obstacles and ASCII floor plans that block motion and radio; water and fog presets; point, unicycle, fixed-wing and quadrotor-lite vehicle models. |
| **Synthetic VO world** | A camera path with typed noise (gaussian, drift, scale, outliers, mixed), dropout and latency, scored with TUM-style ATE and RPE. For testing evaluation chains and noise models. |
| **The lab** | Experiments as YAML, runs as folders (`manifest.json`, `metrics.json`, `report.md`, figures), campaigns, compare, grid/random/Bayesian tuning, a world catalogue with every knob's unit and range. |

## Install

```
claude plugin marketplace add najikay/claude-simlab
claude plugin install sim-lab@claude-simlab
python3 -m pip install numpy pyyaml
```

Or from the Claude directory: install Sim Lab, then run the `pip` line once. The server tells you if the two packages are missing. On Windows, make sure `python3` is on `PATH` (the Python installer's "App execution aliases" or `py` launcher both work; the server runs with whatever `python3` resolves to).

Runs are kept under `~/.simlab/runs/` (set `SIMLAB_HOME` to move them). Your own experiments and floor plans go in `~/.simlab/experiments/` and `~/.simlab/plans/`; the tools `save_experiment` and `save_plan` write there.

## A session

```
you:    Does message loss above 30 % break a 12-drone ring formation?

claude: (reads the catalogue once, picks swarm-formation and the loss0/loss30/loss60 variants,
         says four runs will take about 20 seconds, runs the campaign, compares)

        Up to 30 % loss the formation closes (0.42 → 0.47 m mean slot error); at 60 % it does not
        (1.95 m) and the first collisions appear. Runs 20261003-010101-a1b2…, …

        | run | msg_loss | formation_error_m | collisions |
        | … | 0.0 | 0.42 | 0 |
        | … | 0.3 | 0.47 | 0 |
        | … | 0.6 | 1.95 | 1 |

        The break is between 0.3 and 0.6; I can sweep 0.4 and 0.5 with a second seed to pin it.
```

Ask for "a findings note" and the second skill writes the page above with the run ids as citations.

## Tools

| Tool | What it returns |
|---|---|
| `catalogue` | Every world: what it simulates and what it does **not**, each knob with unit and range, the metrics, which are better when higher, example questions with ready variant sets. Claude reads this first. |
| `list_experiments`, `get_experiment` | The shipped experiments (and your own, flagged `own`), their defaults, variants and YAML. |
| `run_experiment` | One run to completion: run id, status, headline metrics, the report. Overrides by `params` or a named `variant`; an optional `label`. |
| `run_campaign` | Several variants of one experiment, then the compare table. |
| `tune` | Grid, random or Bayesian (GP + expected improvement) search over a parameter space for the best headline metric. |
| `list_runs`, `get_run` | Past runs with their metrics; one run with its manifest, report and folder. |
| `compare_runs` | A table across runs with the best run per metric, direction-aware. |
| `list_plans`, `save_plan` | ASCII floor plans (`#` wall, `D` door, `.` free); saved plans become `layout: plan:<name>`. |
| `save_experiment` | Your own experiment YAML, validated before it is written. |

## Experiments shipped

| Experiment | Question it is built for | Headline metric |
|---|---|---|
| `swarm-formation` | Does the ring close under loss, latency, noise, walls, water, fog, a vehicle model? | `formation_error_m` ↓ |
| `swarm-coverage` | How much of the arena (or the office) gets visited, and at what collision cost? | `coverage_pct` ↑ |
| `swarm-localisation` | Odometry alone vs naive EKF fusion vs covariance intersection, with and without anchors. | `belief_error_m` ↓ |
| `synthetic-vo` | How ATE and RPE grow with each noise model, dropout and latency. | `ate_rmse_m` ↓ |

Each has 8 to 19 named variants (`lossy-comms`, `office-los`, `water`, `fixed-wing`, `anchors2-ci`, `drift`, …). `docs/WORLDS.md` explains the models; the catalogue is the authoritative list of knobs.

## Reproducibility

A run is reproducible from its folder alone:

```
~/.simlab/runs/20261003-010101-a1b2_swarm-formation/
  manifest.json    the exact command, every parameter, the seed, timings, lab version
  stdout.log
  metrics.json     {"headline": {"formation_error_m": 0.42, ...}}
  report.md        parameters, metrics, figures
  trajectory.svg · metrics.svg · agents.jsonl · obstacles.json
```

Seeds are explicit. The skill tells Claude to re-run a close call with another seed before believing it.

## Data handling

- Everything runs on your machine: the MCP server is a local process started by Claude Code; the worlds are Python scripts in this repository.
- Nothing is sent anywhere. The plugin makes no network requests, has no telemetry and needs no account or key.
- What is written: run folders under `~/.simlab` (or `SIMLAB_HOME`), and the experiments and plans you ask Claude to save there. Delete the folder to delete everything.
- The server runs experiments only from the shipped YAML or from files in your lab folder; commands are composed from the experiment's template and typed parameters, never from free text.

## Development

```
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q                      # world, dynamics, lab, server (fake stdio)
ruff check .
claude plugin validate --strict .
claude plugin eval . --runs 1             # the three skill evals under evals/
python3 docs/figures/make_figures.py      # redraw the README figures from fresh runs
```

Running the lab without Claude:

```python
from simlab.lab import Lab

lab = Lab()
r = lab.run("swarm-formation", {"msg_loss": 0.6}, label="loss60")
print(r["headline"])
print(lab.compare([r["run_id"]]))
```

## Limits (honest ones)

The worlds are kinematic and radio-based: no cameras, lidar or terrain; water and fog are presets (drag, a current, slow lossy links; shrunken sensing), not fluid or light models; obstacles stop agents and can block radio, doors do not open; no adversaries. The catalogue's `not` and `missing` entries say the same thing to Claude, so it will not promise what the lab cannot do. `docs/ROADMAP.md` lists what comes next.

## Licence

Apache-2.0. See `LICENSE` and `NOTICE`.
