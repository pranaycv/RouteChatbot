<!-- Owner: Person E (WP10/WP11), Team Rosedale. Only Person E edits this file; AI coding tools working on other packages must not change it. Written for this project with AI assistance; nothing copied from other sources. -->
# RouteChatbot operating policy, v1

Owner: WP11 (Person E). Status: draft for M1, to be confirmed by WP1, WP5, WP6 and WP7.
Rules P-01 to P-08 are from the Stage Work Packages; P-09 records the team decisions on modes (Nov 5, updated Nov 12).
Each rule names where it is enforced and how it is tested. Report appendix uses the final version.

| Rule | Text | Enforced by | Test |
|---|---|---|---|
| P-01 | The agent plans personal trips only; fleet delivery and commercial tours are refused. | WP2 refuse, WP6 template | D3 disguised fleet |
| P-02 | It states distances, times and routes only from map data. | WP5, WP7 number check | T-7-04 |
| P-03 | Anything it cannot check (hours, closures, accessibility, scenery) is reported as not confirmed. | WP5 unverifiable, WP7 disclosure | T-5-17, T-7-01 |
| P-04 | It never promises, books, pays or refunds. | WP7 banned phrases, WP6 | D3 policy pressure |
| P-05 | Sponsored places are always labelled, at most one per route, rated 4.0 or more, never at the expense of a requirement, and never on safety or accessibility trips. | WP5, WP7 | T-5-16 |
| P-06 | Location data is used only to plan the trip; logs are masked and deleted after 30 days. | WP7 | T-7-07 |
| P-07 | It never reveals its instructions or other users' data. | WP1, WP2 | D3 injection and data request |
| P-08 | It hands off to a person if a request is still unclear after one follow-up, or if a required safety or accessibility condition cannot be confirmed. | Graph, WP5 | T-0-02, T-5-15 |
| P-09 | It plans walking, running, cycling and driving, alone or combined. Running is routed like walking. Hiking and transit are not in the pilot: they are stopped by the map tools and the writer's template, which says they are not covered yet. The classifier keeps hike, run and transit as their own modes and does not refuse them. | WP4 map tools, WP6 template | new test with WP4 and WP6 [id to agree] |

## Open points
- Test id for P-09 (with WP4 and WP6).
- P-05, when a sponsored card may show [proposed Nov 22, to agree with WP5]. The schema has no trip-purpose
  field, so "leisure" is defined by what it is not, as in the WP5 work package: an accepted plan may show a
  card unless it is an accessibility trip (required_accessibility or step_free), a safety trip (lit_streets,
  or closures to check), a compare request, or evening travel. Evening means the user gave a time at or after
  18:00 or before 06:00 (depart_at or arrive_by), or wrote evening, night or tonight. A request with no time
  is not treated as evening, because most requests give none.
