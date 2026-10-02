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
<the `run_experiment` or `run_campaign` call, with params, that produced the table>
```

Rules: every number comes from `compare_runs` or `get_run`; run ids are the citation; no claim about the real world beyond what the catalogue says the world models.
