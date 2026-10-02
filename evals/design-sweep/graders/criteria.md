---
type: llm
weight: 1
---

A successful response designs a one-knob sweep, not a shotgun:
- Uses the swarm-formation experiment and sweeps msg_loss only, with 3–6 values spanning the range (e.g. 0, 0.2, 0.4, 0.6, 0.8), everything else at defaults, 12 agents.
- Names formation_error_m as the metric and states that lower is better.
- Gives a cost estimate (a handful of runs, seconds each) and keeps the first campaign to about ten runs or fewer.
- Says how it would decide "stops closing" (a threshold on formation_error_m, or the loss at which the error stops improving) and mentions re-running the boundary with another seed.
- Does not claim results or invent numbers, since nothing was run.
A response that sweeps several knobs at once, picks coverage_pct as the metric, or reports made-up results fails.
