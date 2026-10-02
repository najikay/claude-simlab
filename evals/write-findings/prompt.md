---
max_turns: 8
allowed_tools: [Skill]
---

Write this up as a findings note I can keep. Question: does a Kalman belief help a swarm hold formation when self-localisation is noisy? Runs (swarm-formation, 12 agents, 400 ticks, obs_noise 1.0 m, seed 7, lab 0.1.0, 2026-10-03):

- 20261003-020000-1111_swarm-formation, label belief=exp: formation_error_m 1.84, belief_error_m 0.71, collisions 2
- 20261003-020010-2222_swarm-formation, label belief=kf: formation_error_m 0.93, belief_error_m 0.38, collisions 0
- 20261003-020020-3333_swarm-formation, label belief=kf fuse_neighbours=1: formation_error_m 0.88, belief_error_m 0.31, collisions 0

Catalogue: formation_error_m and belief_error_m are lower-is-better; the world has no terrain or cameras, sensing is a noisy position fix and radio messages.
