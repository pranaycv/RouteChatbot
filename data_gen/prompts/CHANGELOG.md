# Prompt changelog

Format: version | what changed | why | metric before -> after

- gen_v1 / relabel_v1 / gen_d2_v1 / gen_d3_v1 | first versions | initial | n/a
- label_guide_v1.md: first full guide, written from the Nov 22 adjudication (replan rule, escalate triggers, live-data definition, transit rule, follow-up turns). label_guide_draft.md is superseded.
- gen_v2 | added rule 7 <<OMIT_RULE>>: for clarify rows the message must leave out the missing origin/destination/stop | pilot (200 jobs): 12 of 23 clarify messages named the 'missing' field, so the relabeller correctly said plan | disagreement 70% -> to be re-measured
- relabel_v2 | primary_mode list now includes multimodal | v1 could not output multimodal, so 16 of 20 multimodal pilot records disagreed | to be re-measured
- label guide in prompts now label_guide_v1.md (replaces draft): explicit live-data, plan/replan, escalate rules | pilot: needs_live_data and route_action were the most disputed fields
- gen_v3 | rule 8 <<REFUSAL_RULE>>: refusal messages must carry a real cue for their reason; follow-ups reuse the known places via placeholders (landmark = known destination, landmark_2 = known origin, park = first stop) | pilot 2 (194 kept): 63% disagreement; refusal reasons were often missing from the text and 12 of 22 turn-1 messages named places unrelated to `known` | disagreement 63% -> to be re-measured
- relabel_v3 | explicit decision order (plan is last), live-data definition restated | relabeller turned 51 clarify/lookup/compare/refuse rows into plan | to be re-measured
- pipeline (no prompt change): relabel is now a 2-of-3 vote (2nd and 3rd model only called if the 1st disagrees); needs_live_data is also checked by a regex cue and auto-corrected when cue + majority both contradict the generator; QC adds refusal_cue_missing and a turn-1 omitted-field check
- relabel_v3 (amended before the full run): added the compare_modes first-named-mode rule with 3 examples | pilot 3: 31 primary_mode disagreements, mostly car vs unspecified/walk on compare messages | to be re-measured
