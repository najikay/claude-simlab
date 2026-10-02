---
name: simlab-findings
description: Write a Sim Lab result up as a short, reproducible findings note (question, setup, table, answer, limits) from the run ids and compare table. Use after a campaign or sweep when the user wants the result kept, shared or cited.
---

# Findings note

One page per question, from the tools' numbers only.

```
# <the question, as asked>

**Answer.** <one sentence with the number that decides it and its unit>

## Setup
- experiment: <name> (variants or the swept knob and its values); everything else at the defaults
- runs: <run ids>  · lab <version> · <date>
- metric: <headline metric> (<higher|lower> is better, per the catalogue)

## Results
| run | <knob> | <metric> | <second metric> |
|---|---|---|---|
| ... | ... | ... | ... |

## Reading
<two or three sentences: the trend, the size of the effect, anything surprising; a seed re-run if one was made>

## Limits
<what the world does not model that bears on this answer (from the catalogue's `not`); the knob you did not sweep>

## Reproduce
<one line per run, as a tool call: `run_experiment(name="swarm-formation", params={"obs_noise": 1.0, "belief": "kf", "seed": 7}, label="belief=kf")`,
 or one `run_campaign(name=..., variants=[...], params={...})` when the runs were a campaign>
```

Rules: every number comes from `compare_runs` or `get_run` (or from the user's message when the tools are not available; say so in one line); differences and percentages worked out from those numbers are fine, new measurements are not; run ids are the citation, in full; no claim about the real world beyond what the catalogue says the world models. Write the Reproduce lines from the setup you were given even when you cannot run anything: the call shape above is all that is needed.
