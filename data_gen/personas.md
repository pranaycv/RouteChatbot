# Personas (WP8)

Twelve personas. Each says HOW the person writes, not just who they are, because
the persona text is pasted into the generation prompt. `spec.py` reads the
`## <id>` headings; the paragraph below each heading is the persona description.
Add traits here (not in code) to change how the data sounds.

## tourist
Out-of-town visitor, excited and a bit disorganised. Mixes sights and food in one
message, names places by their popular nickname, sometimes misspells local names.
Medium length, one or two exclamation marks at most.

## commuter
Practical, writes in short matter-of-fact sentences. Cares about time saved and
reliability, mentions a work or school deadline. Uses abbreviations like "ETA",
"rush hour". Rarely says please.

## safety_cyclist
Careful and a bit anxious about traffic. Talks about "protected lanes", "no
highways", "not near trucks". Writes complete sentences with a clear list of
conditions. Occasionally explains a past bad experience in one clause.

## parent_with_kids
Tired parent. Mentions kids' ages, naps, snacks, stroller, a playground. Sentences
get cut off or restart. Often adds a time limit ("we have 90 minutes").

## wheelchair_user
Direct and precise about access needs: "step-free", "curb cuts", "working
elevator". Asks for what is known and what is not. No self-pity, no filler.
Frustrated by vague answers, so asks for specifics.

## ev_driver
Tech-aware, uses range numbers and charger vocabulary ("fast charger", "150 km
left", "arrive with 20%"). Compact, numeric, sometimes writes "km/h" and "kWh".

## runner
Training-focused. Gives distances and paces ("10k", "easy 5"), cares about hills
and road crossings, writes lowercase, quick and clipped.

## polite_older_user
Very polite, over-explains, full sentences, "please" and "thank you", sometimes
adds a greeting and a sign-off. May describe landmarks instead of naming them
("the big church near the market"). Avoids slang and emoji.

## terse_phone_typer
Thumb-typing: no punctuation, lowercase, missing letters, fragments, one-line
messages of 4 to 10 words. Autocorrect-style typos.

## angry_user
Annoyed, often because a previous trip went wrong. CAPS for emphasis, blunt
demands, may swear mildly ("damn", "ridiculous"; nothing stronger). Sometimes
demands a person or accuses the app of lying.

## non_native_english
Capable but non-native English: article and preposition slips, literal phrasing,
occasionally a word order that sounds off. Polite and clear, no slang.

## young_user
Casual, slangy, Gen Z register: "lowkey", "rn", "ngl", emoji now and then, no
capitals. Plans spontaneous trips with friends.
