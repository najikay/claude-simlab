---
type: llm
weight: 1
---

PASS if the response judges the auction-greedy gap against the spread: it says the served gap (about 10 points) is larger than either std (4 to 6) but with only three seeds it is not settled, and points at the conflicts (258 against 615) and the messages per task (299 against 494) as the clearer evidence, or asks for more seeds before calling it. FAIL if it calls the gap certain without mentioning the seeds or the spread, or dismisses it as noise.
