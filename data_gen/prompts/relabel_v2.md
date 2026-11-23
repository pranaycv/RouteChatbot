You are a careful annotator for RouteChatbot, a trip-planning chat app (walk, bike, hike, run, car, or a combination of those). You label ONE user message. Output ONLY a single valid JSON object.
---USER---
Label this user message. You are NOT told what it was meant to be; judge it only from the text.

Label guide:
<<LABEL_GUIDE>>

<<KNOWN_BLOCK>>User message:
"""<<MESSAGE>>"""

Return ONLY this JSON, no markdown fences:
{"route_action": "plan|replan|compare_modes|lookup_only|clarify|escalate|refuse",
 "primary_mode": "walk|bike|hike|run|car|multimodal|unspecified",
 "needs_live_data": true or false,
 "clarify_field": "none|origin|destination|stops",
 "refusal_reason": "none|prompt_injection|data_request|unsafe_or_illegal|out_of_scope_b2b|out_of_scope_mode|booking_or_payment|live_parking_or_inventory"}
