# Prompt changelog

Format: version | what changed | why | metric before -> after

- gen_v1 / relabel_v1 / gen_d2_v1 / gen_d3_v1 | first versions | initial | n/a
- label_guide_v1.md: first full guide, written from the Nov 22 adjudication (replan rule, escalate triggers, live-data definition, transit rule, follow-up turns). label_guide_draft.md is superseded.
- gen_v2 | added rule 7 <<OMIT_RULE>>: for clarify rows the message must leave out the missing origin/destination/stop | pilot (200 jobs): 12 of 23 clarify messages named the 'missing' field, so the relabeller correctly said plan | disagreement 70% -> to be re-measured
- relabel_v2 | primary_mode list now includes multimodal | v1 could not output multimodal, so 16 of 20 multimodal pilot records disagreed | to be re-measured
- label guide in prompts now label_guide_v1.md (replaces draft): explicit live-data, plan/replan, escalate rules | pilot: needs_live_data and route_action were the most disputed fields
