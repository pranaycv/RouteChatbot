# Scope notes

What the pilot covers, and how it differs from the first Project Definition.

## Compared with the Project Definition

| Planned | Built | Status |
|---|---|---|
| **Voice Agent** captures requests | Typed chat | **Not built.** It would be speech-to-text in front of the input guard, and no later stage would change. |
| **Metadata Agent** with a fine-tuned classifier finds constraints, priorities and ambiguities | Two stages: a classifier (five labels) and an extractor (the `ConstraintSet`) | Built. The fine-tuned model is one of four classifier backends. The app uses a prompted one by default. |
| **Live Data Agent** checks traffic, closures and places | Map tools: Mapbox places, GraphHopper routes | Partly built. Places yes. Traffic and closures only when Mapbox returns the same route as GraphHopper, otherwise they are reported as unverifiable. |
| **Verification Agent** checks the route | Verifier, in code and without a model | Built: met, unmet or unverifiable. |
| Four agents work together | Eight fixed stages in LangGraph. Python chooses every branch. | Changed on purpose, so that each decision can be tested and repeated. |

## Covered by the pilot

- Walking, running, cycling and driving, one mode per trip (P-09).
- Up to three required stops.
- Typed requests in English.
- Places in Canada (`maps.geocode_country`).
- Personal trips (P-01).

## Not covered

| Request | What the app does |
|---|---|
| Transit | Refuses it with a short reply |
| Hiking, or two modes in one trip | Says it is not covered yet. No support ticket |
| Fleet delivery, commercial tours | Refuses |
| Booking or payment, live parking, store stock | Refuses |
| Saved places such as "home" or "my university" | Asks for the place |
| Another language | Fixed reply |
| Voice | Not available |

The first five depend on the classifier and the extractor reading the request correctly. The evaluation measures how often they do.

## Checked, not steered

The pilot uses GraphHopper's standard routing. It cannot steer a route away from highways or tolls, or towards bike lanes or flat roads, so the verifier measures the route it gets.

- A hard requirement that is not met goes to a person.
- Scenic, quiet, lit and step-free routes, and opening hours at arrival, are reported as unverifiable. If the trip needs step-free access, or asks to check closures by car, an unverifiable result goes to a person.
- The revise loop is built and tested, but no live route reaches it, because standard routing would return the same route.

## Data kept

- Log lines and support tickets are masked, and dropped after 30 days (P-06, `RETENTION_DAYS` in `routechat/logging.py`). Old ones go when a new one is added, or when the support list is opened.
- The device location goes only to the map tools and the map on screen.
