# Changelog

## 0.3.0 (2026-10-07)

The task-allocation world after an independent review of the first results (model changes; numbers from 0.2.x are not comparable).

- Agents act on what they know. A task is open to an agent until it sees or hears it done or its told deadline passes; the true task status is read only by the world (sensing, serving, expiring), the oracle and the metrics. Before, every policy filtered on the true status, so all agents learned at once, arena-wide, when any task was served.
- `shared_map`: 0 (the tasks experiment's default) gives each agent its own visit map from its own position belief for exploration; 1 keeps the true shared map the older coverage experiments used (a centrally coordinated exploration, now a disclosed choice).
- Policies decide from their position belief, not the true position. Bids follow the agent (recomputed from the current distance every decision tick) and an agent at its task keeps it. Before, a bid was frozen at claim time, so an arriving agent could outbid a holder about to serve.
- `greedy-re`: greedy that re-evaluates every decision tick. `random` keeps its task until done (it re-drew every decision tick before: a thrashing control).
- `early_served_pct`: served among tasks that arrived early enough to reach a verdict before the run ended (no censoring by outcome); `early_tasks`.
- `TaskBoard.add_task` for scripts and tests.

## 0.2.4 (2026-10-07)

- No code in the repository, tests included, reads or copies the process environment any more (the test helper that checked an allow-list, and two test-side environment copies, are gone).

## 0.2.3 (2026-10-07)

- The lab reads no environment variable at all: a world process inherits its environment the way any child process does, and UTF-8 output comes from `python -X utf8` on the command instead of two variables. (The directory's scan reads any `os.environ` access in an MCP server as "uses a credential from the user's machine", named or not.)
- `yield`: greedy plus one rule, give way when a neighbour whose position you already hold is nearer; no bids. `cbaa`: the consensus-based auction of Choi, Brunet and How (2009), single assignment. `decided_served_pct`, `tasks_open_at_end`, `blind_pct`, `known_tasks_per_agent`. Dense and ladder variants.

## 0.2.2 (2026-10-07)

- `EXTRA_ALLOC`: an embedding project can add a task-allocation policy without forking (a callable over each agent's view: its known open tasks with distance, seconds to the deadline and the best bid heard); on an error the board falls back to greedy for that decision and says so. The workbench registers `laya` this way.
- With the smooth estimate (0.2.1) the floor sweep on coverage gives a curve: coverage 89 → 79 → 65 → 51 % at floors 0 / 0.1 / 0.3 / 0.8 while the swarm is connected 3 → 54 → 84 → 91 % of ticks (8 agents, 3 seeds).

## 0.2.1 (2026-10-07)

- The connectivity estimate behind `lambda2_floor` is the λ2 of a weighted graph (edge weight falling from 1 to 0 across `comm_range`, after Zavlanos and Pappas), so it changes smoothly as agents move; in 0.2.0 it stepped between 0 and 2 at low degree and the floor's value hardly mattered.
- The catalogue has `msg_budget`, `lambda2_floor`, `connectivity_holds_pct` and the coverage-under-a-floor question; 0.2.0 shipped the knobs without them.

## 0.2.0 (2026-10-07)

Numbers you can report, and a decision to make.

- `repeat`: one configuration over several seeds (default 1..5) with mean, std, min, max and n per headline metric; `run_campaign` takes `seeds` and summarises each variant the same way. A single run is a sample; this is the number.
- `swarm-tasks`: distributed task allocation. Tasks appear in the arena with a service time and a deadline; an agent learns of one by sensing it or hearing of it from a neighbour over the same lossy, delayed links as everything else, then picks one by `alloc`: `greedy` (nearest known), `auction` (bid by distance, a lower bid heard over the links wins, the loser picks again), `oracle` (a central optimal assignment over the true state; the upper bound; sends nothing) or `random` (the control). Metrics: served, missed, served %, service time mean and p90, makespan, conflicts (agent-ticks with two agents on one task), messages per served task, distance per agent. Fourteen variants and two catalogue questions. The Hungarian assignment is pure numpy.
- `msg_budget`: how many neighbours an agent may message each tick (0 = all). Per-message loss is redundancy-proof when every link sends every tick; the budget is the radio constraint under which loss and latency start to matter.
- `lambda2_floor`: planning under a constraint. An agent whose planned move would drop the algebraic connectivity of the graph it can see (itself and the neighbours it holds beliefs about) under the floor holds the mission and moves toward its neighbours; `connectivity_holds_pct` is the price. Known limit: at low degree the local estimate is a step (one neighbour in range → λ2 = 2, none → 0), so the floor's value matters little; a smoother constraint is next.
- The task news travels on the same directed sends as the position beliefs, so one budget, loss and latency govern both.
- 40 tests; a fourth eval case (reading a seeded summary: which policy wins and whether the gap is real).

## 0.1.0 (2026-10-03)

First release, under the PolyForm Noncommercial License 1.0.0 (free for any noncommercial use; commercial use by agreement). Reviewed before submission by an independent agent; everything it found was fixed before the first push: experiment commands run without a shell with every parameter quoted (a value can never add a command), the Bayesian tuning strategy, Python 3.10 support, shipped experiments and plans protected from silent replacement, parameter values checked against the catalogue's ranges, the catalogue's example questions backed by real variants, the tuning objective and its direction checked, failed runs returned with their log, floor-plan typo rows reported instead of dropped, unreadable files in the lab folder skipped, interrupted runs marked, ties without a "best", JSON-RPC edge cases.

- Swarm world: flock, formation, rendezvous, coverage, goto; lossy and delayed range-limited radio with line-of-sight blocking; exponential, Kalman and unicycle-EKF beliefs with range-bearing fusion (naive and covariance intersection) and anchors; pillars, rooms, corridor and ASCII floor plans (office, warehouse, maze); water and fog presets; point, unicycle, fixed-wing and quadrotor-lite vehicles.
- Synthetic visual odometry world with gaussian, drift, scale, outlier and mixed error models, dropout and latency, scored by ATE/RPE.
- The lab: experiments as YAML, runs as folders with manifest, metrics, report and figures; campaigns; direction-aware compare; grid, random and Bayesian tuning; a world catalogue; floor plans and experiments of your own under `~/.simlab`.
- `sim-lab` MCP server (stdio, stdlib) with twelve tools.
- Extension points in the swarm simulator (`EXTRA_POLICIES`, `EXTRA_ARGUMENTS`) so an embedding project can add a decision policy without forking.
- Skills `simlab-run` and `simlab-findings`; three evals (all 1.00 with the plugin over three runs each; 0.33 / 1.00 / 0.00 without); 28 tests on Linux and Windows for Python 3.10 and 3.12.
