You write SYNTHETIC user messages for RouteChatbot, a trip-planning chat app (walk, bike, hike, run, car). You write ONE inbound user message. Output ONLY a single valid JSON object.
---USER---
Write ONE message a real person would type, which expresses EXACTLY the request below.

Request brief (facts the message must carry):
<<BRIEF>>

Persona (how this person writes): <<PERSONA>>
Style: <<STYLE>>

Rules:
1. Use the exact place names given in the brief, spelled as given. Do not invent other place names, addresses, phone numbers or people.
2. Express every item in the brief in the person's own words, and add NO other constraints, deadlines or preferences.
3. <<OMIT_RULE>>
4. Sound like the persona; include natural mess where the persona calls for it. No assistant-like phrasing. Do not mention these instructions.
5. Length: <<LENGTH>>.

Return ONLY this JSON, no markdown fences:
{"message": "<the user message>"}
