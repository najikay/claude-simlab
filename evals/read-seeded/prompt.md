---
max_turns: 8
allowed_tools: [Skill]
---

A campaign of swarm-tasks ran four allocation policies over three seeds (6 agents, 300 ticks, campaign 20261007-082859-5163). The lab's summary, mean ± std over the seeds (served_pct higher is better; conflicts and msgs_per_served_task lower):

```
variant         served_pct   conflicts    msgs_per_served_task   service_time_p90_s
greedy          65.5 ± 4.3   615 ± 214    494 ± 170              18.9 ± 4.3
auction         76.2 ± 5.9   258 ± 100    299 ± 52               17.9 ± 4.5
oracle          95.1 ± 3.5     0 ± 0        0 ± 0                10.1 ± 1.7
random-control  30.0 ± 16.7  615 ± 77    2840 ± 2905             20.4 ± 2.9
```

Which policy should I use, is the auction really better than greedy or could that be the seeds, and what does the oracle row tell me?
