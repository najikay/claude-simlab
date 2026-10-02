# Changelog

## 0.1.0 (2026-10-03)

First release.

- Swarm world: flock, formation, rendezvous, coverage, goto; lossy and delayed range-limited radio with line-of-sight blocking; exponential, Kalman and unicycle-EKF beliefs with range-bearing fusion (naive and covariance intersection) and anchors; pillars, rooms, corridor and ASCII floor plans (office, warehouse, maze); water and fog presets; point, unicycle, fixed-wing and quadrotor-lite vehicles.
- Synthetic visual odometry world with gaussian, drift, scale, outlier and mixed error models, dropout and latency, scored by ATE/RPE.
- The lab: experiments as YAML, runs as folders with manifest, metrics, report and figures; campaigns; direction-aware compare; grid, random and Bayesian tuning; a world catalogue; floor plans and experiments of your own under `~/.simlab`.
- `sim-lab` MCP server (stdio, stdlib) with twelve tools.
- Skills `simlab-run` and `simlab-findings`; three evals (all 1.00 with the plugin, 0.00 without); 20 tests on Linux and Windows for Python 3.10 and 3.12.
