<!-- Owner: Person E (WP10/WP11), Team Rosedale. Only Person E edits this file; AI coding tools working on other packages must not change it. Written for this project with AI assistance; nothing copied from other sources. -->
# Status quo study (WP11 Part B)

Purpose: measure how long it takes a person to plan a D2 trip by hand today, and how often the plan
misses a requirement. The decision memo compares this with the agent's time and trip success rate.
The point is not to beat Google Maps on the route itself. It is the time spent finding the stops,
putting them in order and checking every requirement (bike lanes, hills, hours, closures), which the
agent does for you and then verifies (agreed at the Nov 12 meeting). The main number is minutes saved.

Who: two teammates, 10 trips each, 20 trips in total. The two people take different trips.
Which trips: picked by WP11 from D2, spread over the core use cases (sightseeing, attraction, cycling,
hike, road trip). Walking, cycling and driving only.

For each trip:
1. Start a timer. Plan the trip the way you normally would: map apps, the venue's website, blogs, forums.
2. Stop the timer when you would be ready to leave. Write the minutes in minutes_spent.
3. Fill in each check column with yes, no or na (not asked for in this request).
   all_requirements_met is yes only if every requirement in the request is met.
4. In what_was_missed, write anything you skipped or could not check, for example "did not check closures".

Rules:
- Do not use our chatbot or any AI assistant for this. The point is the manual baseline.
- Do not look at the gold answer in D2 before planning.
- Save the filled CSV as report/status_quo_study.csv (one row per trip).

Time needed: about 5 to 10 minutes per trip [estimate], so 1 to 2 hours per person.
