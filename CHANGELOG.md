# Changelog

## 0.1.0 (2026-10-03)

First release, under the PolyForm Noncommercial License 1.0.0 (free for any noncommercial use; commercial use by agreement). Reviewed before submission by an independent agent; everything it found was fixed before the first push: experiment commands run without a shell with every parameter quoted (a value can never add a command), the Bayesian tuning strategy, Python 3.10 support, shipped experiments and plans protected from silent replacement, parameter values checked against the catalogue's ranges, the catalogue's example questions backed by real variants, the tuning objective and its direction checked, failed runs returned with their log, floor-plan typo rows reported instead of dropped, unreadable files in the lab folder skipped, interrupted runs marked, ties without a "best", JSON-RPC edge cases.

- Swarm world: flock, formation, rendezvous, coverage, goto; lossy and delayed range-limited radio with line-of-sight blocking; exponential, Kalman and unicycle-EKF beliefs with range-bearing fusion (naive and covariance intersection) and anchors; pillars, rooms, corridor and ASCII floor plans (office, warehouse, maze); water and fog presets; point, unicycle, fixed-wing and quadrotor-lite vehicles.
- Synthetic visual odometry world with gaussian, drift, scale, outlier and mixed error models, dropout and latency, scored by ATE/RPE.
- The lab: experiments as YAML, runs as folders with manifest, metrics, report and figures; campaigns; direction-aware compare; grid, random and Bayesian tuning; a world catalogue; floor plans and experiments of your own under `~/.simlab`.
- `sim-lab` MCP server (stdio, stdlib) with twelve tools.
- Skills `simlab-run` and `simlab-findings`; three evals (all 1.00 with the plugin over three runs each; 0.33 / 1.00 / 0.00 without); 28 tests on Linux and Windows for Python 3.10 and 3.12.
