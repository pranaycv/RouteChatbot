# Prompt changelog

Each prompt change records what changed, why, and the metric before and after.

## writer_v1

- What: first writer prompt. Fixed early-exit sentences stay in code. The model only describes an accepted route, or a handoff that still has a route.
- Why: WP6. The wording follows policy P-03 (say what was not confirmed), P-04 (no booking or promise), P-05 (label a sponsor), and P-08 (a handoff says a person has the trip).
- Metric before: none. This is the first version.
- Metric after: not measured. Faithfulness and disclosure completeness wait for the WP10 judge.
