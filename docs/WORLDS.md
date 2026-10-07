# The worlds

Sim Lab ships two worlds. The catalogue (`simlab/catalogue.yaml`) is the authoritative list of knobs, units, ranges and metrics; this page explains the models behind them.

## Swarm

`simlab/worlds/swarm/sim.py`, `world.py` (obstacles, floor plans, line of sight), `dynamics.py` (media and vehicle models).

**Agents.** N point agents in a square arena of side `arena` metres, stepped in discrete ticks of 0.1 s. Each agent holds a *belief* of its own position, built from a noisy position fix (`obs_noise`, metres sigma) through one of three filters:

- `exp`: an exponential filter with gain `belief_alpha`;
- `kf`: a constant-velocity Kalman filter (`kf_q` scales the process noise); with `fuse_neighbours: 1` the neighbours' observations of this agent are fused as extra measurements;
- `ekf`: a unicycle EKF driven by noisy odometry (`odom_noise`), a position fix only every `fix_every` ticks (`anchors` agents get one every tick), and range + bearing to neighbours whose broadcast beliefs act as landmarks. `rb_fusion: naive` does the plain EKF update (assumes independence; can diverge because the neighbours' beliefs were built partly from this agent's own messages); `ci` uses covariance intersection, which is consistent under unknown correlation and therefore never diverges and is never the sharpest.

**Radio.** Every pair within `comm_range` metres exchanges beliefs each tick. Each message is lost with probability `msg_loss` and delayed by `msg_latency` ticks. With `comms: los`, a wall between two agents blocks the link as well (the `blocked_links_pct` metric counts those pairs). Connectivity metrics: `connected_pct` (ticks on which the graph was one piece), `mean_lambda2` (algebraic connectivity), `mean_neighbours`.

**Behaviours.** `flock` (alignment, cohesion, separation), `formation` (consensus on ring slots of radius `formation_radius`; `formation_error_m` is the mean distance from the slot), `rendezvous` (`consensus_error_m`), `coverage` (each agent heads for its least-visited cell of a `coverage_cells` × `coverage_cells` grid; `coverage_pct`), `goto` (greedy target allocation). The `policy` decides each agent's behaviour every `decision_every` ticks: `rules` (separation first, then the mission, rendezvous when isolated) or `random` (a control).

**Tasks (mission `tasks`).** `simlab/worlds/swarm/tasks.py`. Tasks appear at random positions (`task_count` at the start, then `task_rate` per 100 ticks, Poisson); one is served when an agent stays within `task_radius` of it for `task_size` seconds, and missed when `deadline` seconds pass unserved. An agent learns of a task only by sensing it within `sense_range` or by hearing of it from a neighbour: every message also carries the task ids the sender knows, the ids it knows are done, and its claim, so task news has the same loss, latency and budget as everything else. Nothing a decentralised policy does reads the true state: a task stays open for an agent until it sees or hears it done or its deadline passes, bids are the agent's current distance from its own position belief, and an agent within `task_radius` of its task keeps it. Policies (`alloc`): `greedy` (nearest known open task, kept), `greedy-re` (re-evaluates every decision tick), `yield` (greedy plus give way when a neighbour whose position you hold is nearer; no bids), `auction` (one hop per tick, remembered best bids), `cbaa` (Choi, Brunet and How 2009, single assignment, max-consensus on winner lists), `random` (the control) and `oracle` (a central Hungarian assignment from the true state every decision tick, reads no message: a reference). With the radio off every decentralised policy reduces to "take what you sense". An agent with a task heads to it; one without explores by the least-visited cell of its own visit map (`shared_map: 0`; with 1, one map for the swarm, which is a leak of true positions and exists only for comparison). There is no inter-agent avoidance in this mission: agents pass through each other and `collisions` counts pairs within `collision_r`. Metrics: `early_served_pct` (served among tasks that arrived early enough to reach a verdict before the run ended; the honest throughput), `decided_served_pct` (served over served + missed), `served_pct`, `conflicts` (agent-ticks with two or more agents on one task), `blind_pct` (open task-ticks no agent knows of), `known_tasks_per_agent`, `msgs_per_served_task`, `tasks_open_at_end`.

**Obstacles and plans.** `layout: pillars | rooms | corridor` are built in; `plan:<name>` loads an ASCII floor plan from the shipped plans or your lab's `~/.simlab/plans/` folder (saved with `save_plan`) (`#` wall, `D` door, `.` free; the grid is scaled so its longer side spans the arena, so a 20-column plan on a 40 m arena has 2 m cells). Agents sense walls within `sense_range` and steer away; an agent that still hits one is stopped (`obstacle_hits`). Doors are static openings.

**Media.** `air` changes nothing. `water` adds 40 %/s drag, a `current` (m/s), and acoustic links: half the range, +5 ticks latency, +20 % loss. `fog` touches sensing only: obstacles seen at 40 % range, half the range/bearing measurements missed and the rest twice as noisy.

**Vehicles.** The demanded velocity becomes motion through `point` (mass point with speed and acceleration limits), `unicycle` (no sideways motion, 2 rad/s turn limit), `fixed-wing` (must keep at least half of the maximum speed, 0.9 rad/s turns), `quadrotor-lite` (half the acceleration, slight drag). These are kinematic limits, not dynamics.

**Not modelled.** Cameras, lidar, terrain, light, fluid dynamics, opening doors, adversaries. See the catalogue's `not` and `missing` entries.

**Your own decision policy.** A project that embeds the simulator can add a policy without forking it: load `sim.py` as a module, put a function in `EXTRA_POLICIES` (called once per decision tick with every agent's view and the parsed arguments, returning one `(behaviour, confidence, source)` per view), optionally add flags through `EXTRA_ARGUMENTS`, then call `main()`. If the policy raises, that tick falls back to the rules and the run continues. Nothing is registered in the plugin itself.

## Synthetic visual odometry

`simlab/worlds/vo/synthetic_vo.py`.

A true camera path (`shape: circle | lemniscate`, `steps` poses) and an estimate derived from it by a chosen error model: `gaussian` white position noise (`noise`), `drift` heading drift per step (`drift`), `scale` relative step-length error (`scale_err`), `outliers` 10× jumps on a fraction of steps (`outlier_rate`), or `mixed` (all of them). `dropout_rate`/`dropout_len` remove windows of estimate poses; `latency` stamps the estimate late so association suffers. Both trajectories are written as TUM files and scored by `simlab/evaluate.py`: ATE RMSE after alignment, RPE over one step, and drift as a percentage of path length.

Orientation is not modelled: every pose carries the identity quaternion, so where the camera points plays no part, and ATE and RPE are computed on positions only. There is no image and no estimator: this world exists to test evaluation chains, noise models and the run → metrics → compare contract with something that finishes in well under a second (`rate: 0`; a positive `rate` paces the run for a live viewer).

## Adding a world

A world is any command that writes into `{run_dir}`. The template is split into words like a POSIX shell line and run without a shell, with every parameter value quoted as one word: write paths with forward slashes, or pass a path as a parameter (a Windows path with backslashes survives there). Write an experiment YAML (`name`, `command` with `{python}`, `{repo}`, `{run_dir}` and one `{placeholder}` per parameter, `params` defaults, `variants`, and either `outputs: {est, gt}` TUM files for trajectory scoring or a `metrics.json` with a `headline` object written by the command). Save it with the `save_experiment` tool or drop it in `~/.simlab/experiments/`. Add the world to `simlab/catalogue.yaml` so Claude knows its knobs and limits.
