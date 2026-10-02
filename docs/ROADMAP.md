# Roadmap

What Sim Lab does not do yet, roughly in the order it would be useful. Issues and pull requests welcome.

- **Doors that open and close** during a run (plans are static today).
- **A 3-D arena** for the swarm world (altitude, a terrain height map, quadrotor dynamics beyond the kinematic preset).
- **Lidar and camera-like sensing** (range scans against the floor plan; a feature-count model for VO).
- **Real estimators in the VO world**: plug a trajectory file from your own pipeline into the same ATE/RPE scoring.
- **Pursuit and interception scenarios** (adversaries), kept out deliberately for now.
- **Live view**: `rate > 0` already paces a run for a viewer; a small local page that follows `agents.jsonl` is the natural next step.
- **Statistics across seeds**: `tune` already runs repeats; a `repeat` tool that returns mean ± sd per metric would make the "is this real?" check one call.
- **Export**: a campaign as CSV, a run as a zip with everything needed to cite it.
