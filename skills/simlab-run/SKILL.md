---
name: simlab-run
description: Turn a robotics question into a simulation experiment with the Sim Lab tools, run it, compare the runs and answer with numbers. Use when the user asks what happens to a swarm, a formation, coverage, localisation or visual odometry under some condition (message loss, latency, noise, walls, water, fog, a vehicle model), or wants an experiment designed, tuned or reproduced.
---

# Sim Lab: from a question to a finding

The lab runs on the user's machine through the `sim-lab` tools. A run is a folder with the exact command, every parameter, `metrics.json` and `report.md`, so anything you report can be reproduced.

## Procedure
1. **Read the catalogue first** (`catalogue`, once per conversation). It says what each world simulates and what it does not, every knob with its unit and range, the metrics, which metrics are better when higher, and example questions with ready variant sets. Do not promise what a world cannot do (no cameras, no terrain, no adversaries, static doors).
2. **Pick the experiment and the knob** that the question is about. Prefer a shipped variant set when one answers the question; otherwise choose 3–5 values of one knob spanning its range, everything else at the defaults. One knob at a time: a sweep answers a question, a shotgun does not.
3. **Estimate before running**: a swarm run of 400 ticks with 12 agents takes a few seconds; 1000 ticks or 40 agents, tens of seconds. Keep a first campaign under about ten runs and say how long it will take.
4. **Run**: `run_campaign` for named variants, `run_experiment` with `params` for a sweep (give each run a `label`), `tune` only when the user wants a best value of a metric (say the budget).
5. **Compare** with `compare_runs`. Read the direction of each metric from the catalogue's `higher_is_better` before calling anything better. Report the headline metric per run in a table, the best run, and the size of the difference in the metric's unit.
6. **Check the result is not an accident**: if two runs differ by less than you would expect from the seed, run the top two again with another `seed` before concluding.
7. **Answer the question in one sentence**, then the table, then what would change the answer (a knob you did not sweep, a world limit from the catalogue's `not`). Write it up with the `simlab-findings` skill when the user wants it kept.

## Rules
- Numbers come from `metrics.json` through the tools, never from memory or from what "should" happen.
- Say the run ids; they are the citation.
- When the user's folder has its own experiments (`own: true` in `list_experiments`), use theirs over the shipped one of the same name.
- A failed run (`status: error`) is reported with its `error` and the `stdout_tail` the tools return (the world's own message), not retried blindly. A value outside the catalogue's range is refused before anything runs: pick one inside it.
