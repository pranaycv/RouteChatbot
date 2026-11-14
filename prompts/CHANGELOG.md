# Prompt change log

Every prompt change gets an entry: what changed, why, and the metric before and after (handbook A2).

## label_guide_v1.md

- **2026-11-13, v1.** First version. Written from the handbook's WP2 label rules and the A1 controlled vocabularies, matched to schemas v1 (`routechat/schemas.py`) and operating policy P-09: hike and transit are labelled as themselves and are not refused; the map tools and the writer turn them away. Decisions the handbook left open, to confirm with WP8: both ends missing gives `clarify_field = origin`; `clarify_field = stops` only when a stop is requested but its kind is unknown; `primary_mode` and `needs_live_data` are still labelled on refuse, escalate and clarify turns; EV and car-share are `car`. No metric yet; the frozen D1 test set is not available.
