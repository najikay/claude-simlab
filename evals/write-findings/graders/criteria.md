---
type: llm
weight: 1
---

A successful response is a note with the sections: the question as a title, a one-sentence answer with the deciding number (formation error 1.84 m → 0.93 m with the Kalman belief, 0.88 m with neighbour fusion), Setup (experiment, swept knob, seed, lab version, date, metric with its direction), a Results table with the three runs and their run ids, a Reading paragraph (the Kalman belief roughly halves formation error and removes the collisions; neighbour fusion adds a small further gain), Limits (the world models a noisy position fix and radio only, no cameras or terrain; one seed), and a Reproduce block with the call that made the runs.
It must not invent measurements that were not given (differences or percentages computed from the given numbers are fine), must keep the three run ids, and must not claim anything about real drones beyond what the catalogue says the world models. The Reproduce block may be a `run_experiment(...)` call per run or one `run_campaign(...)` call, with the parameters that made the runs.
