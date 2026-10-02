---
max_turns: 8
allowed_tools: [Skill]
---

I have a swarm of 12 drones that must hold a ring formation while their radios drop messages. I want to know at what message-loss rate the formation stops closing. The lab's tools are not available in this session, so do not run anything: design the experiment I should run, as you would before calling the tools. Here is the relevant part of the catalogue:

```
world: swarm — experiments: swarm-formation, swarm-coverage, swarm-localisation
knobs: agents (count 2-60), ticks (10-3000), comm_range (m, 2-100; 20 keeps 12 agents on 40 m connected), msg_loss (probability 0-0.9), msg_latency (ticks 0-30), obs_noise (m, 0-3), layout (none|pillars|rooms|corridor|plan:office|plan:warehouse|plan:maze), comms (range|los), medium (air|water|fog), vehicle (point|unicycle|fixed-wing|quadrotor-lite), seed
metrics: formation_error_m (lower is better), coverage_pct (higher is better), collisions, msg_loss_pct, connected_pct (higher), mean_lambda2 (higher)
a swarm run of 400 ticks with 12 agents takes a few seconds
```
