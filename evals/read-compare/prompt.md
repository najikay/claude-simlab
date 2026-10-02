---
max_turns: 8
allowed_tools: [Skill]
---

Here is the compare table the lab returned for a message-loss sweep on swarm-formation (12 agents, 400 ticks, seed 7). Tell me what it says and which run is best. The catalogue says: formation_error_m lower is better; connected_pct higher is better; coverage_pct higher is better.

```
run_id                                  label          formation_error_m  connected_pct  coverage_pct  collisions
20261003-010101-a1b2_swarm-formation    msg_loss=0.0   0.42               100.0          31.0          0
20261003-010108-c3d4_swarm-formation    msg_loss=0.3   0.47               100.0          30.5          0
20261003-010115-e5f6_swarm-formation    msg_loss=0.6   1.95               100.0          29.8          1
20261003-010122-a7b8_swarm-formation    msg_loss=0.8   6.80               100.0          28.1          3
best: formation_error_m → 20261003-010101-a1b2_swarm-formation ; coverage_pct → 20261003-010101-a1b2_swarm-formation
```
