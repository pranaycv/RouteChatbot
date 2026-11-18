# RouteChatbot

Conversational route planner for MMAI 894. A request passes through seven stages. LangGraph runs them in order and chooses every branch in Python. Only the classifier, the constraint extractor, and the reply writer call a model.

This repository currently contains WP0 (the shared contracts, the configuration, and the orchestrator), WP1 (the input guard), WP6 (the response writer), and WP10/WP11 (evaluation, the cost model, and the operating policy). The other stage packages plug in beside them as they are merged.

## Install

```bash
uv sync
```

Python 3.11. `uv sync` installs the locked core and dev dependencies. Training libraries stay out of that environment:

```bash
uv sync --group finetune
```

Copy `.env.example` to `.env` and fill in the keys locally. `.env` is never committed.

## Run

WP7 owns the Streamlit app. Once that package is merged, the app command is:

```bash
uv run streamlit run routechat/app.py
```

Until then, the orchestrator runs the week-1 stub end to end on the handbook's running example:

```bash
STUBS=1 uv run python -m routechat
```

`STUBS=1` returns the running example from every stage, so the flow works before those stages exist. Leave it unset to load the real stage callables.

## Reproduce the evaluation

WP10 owns the evaluation entry point:

```bash
FIXTURES=replay uv run python -m eval.run_eval --all
```

Each step prints `waiting for ...` until the dataset or stage package it needs is merged. `eval/README.md` lists the WP10 and WP11 files and the cost-model command.

The tests for every merged package run with:

```bash
FIXTURES=replay uv run pytest
```

`FIXTURES=replay` reads recorded API responses, so these commands do not call Mapbox or GraphHopper and do not spend credits.

## Layout

```text
config.yaml              thresholds, model names, prices
routechat/config.py      typed access to config.yaml
routechat/schemas.py     shared contracts, schemas v1
routechat/graph.py       stage order and every branch
routechat/stubs.py       running-example output for STUBS=1
routechat/guards/        WP1 input guard
routechat/agents/writer.py WP6 reply, template or one model call
prompts/writer_v1.md     WP6 prompt
prompts/CHANGELOG.md     prompt changes
prompts/judge_v1.md      WP10 judge prompt
tests/test_graph.py      T-0-01 to T-0-05
tests/test_input_guard.py T-1-01 to T-1-10
tests/test_writer.py     T-6-01 to T-6-04
eval/                    WP10 metrics and run_eval; WP11 cost model
policy/                  operating policy v1
report/                  status quo study template
tests/test_eval.py       T-10-01 to T-10-09, T-11-01 to T-11-03
```

Schemas v1 are frozen. A contract change is a pull request reviewed by the integration lead and by every package that reads the object. Stage code depends on these objects, not on another stage's internals.

## Plugging in a stage

One branch per package (`stage/maps`, `stage/verifier`, …). A node is `def node(state) -> dict` and returns only the fields it owns. It does not call another stage. With `STUBS` unset, the graph imports:

| Node | Callable |
| --- | --- |
| `guard_in` | `routechat.guards.input_guard:input_guard` |
| `classify` | `routechat.classifier.predict:classify_node`, or `classify(turn) -> RouteClassification` |
| `extract` | `routechat.agents.extractor:extract_node` |
| `maps` | `routechat.agents.maps_node:maps_node` |
| `verify` | `routechat.verify.decide:verify_node` |
| `write` | `routechat.agents.writer:write_node` |
| `guard_out` | `routechat.guards.output_guard:output_guard` |

The app calls the graph with the user's text and the session values it kept from the previous turn:

```python
from routechat.graph import invoke

state = invoke({
    "request_id": "r_0193",
    "utterance": "Plan a bike route from King Station ...",
    "turn_index": 0,
    "known": None,
    "clarify_count": 0,
    "revision_count": 0,
})
answer = state["final"]
ticket = state.get("handoff")
```

`deadline`, when the app sets it, is a `time.monotonic()` timestamp. If it is omitted, the graph sets it 30 seconds ahead (`graph.request_budget_s`). An exception or a spent budget becomes a handoff with reason `system_error` or `timeout`. The log records the exception type and never the raw request.

## Branches

`allow` goes to the classifier. `clarify`, `refuse`, and `fixed_reply` go to the writer. `plan`, `replan`, `compare_modes`, and `lookup_only` go to the extractor. A complete constraint set goes to the map tools; a missing field or too many stops goes to the writer. The verifier may send the request back to the map tools once. A second `revise` does not loop. A clarify that is still unclear after one follow-up (`clarify_count >= 1`) is written as `escalate`.

## Decisions other packages depend on

These are fixed with schemas v1. A change is a pull request reviewed by the integration lead and by every package that reads the object.

- **Null is not zero, and null is not an empty list.** On a route, `closures: null` means the provider did not say. `closures: []` means it checked and found none. The same rule applies to opening hours: null means unknown, not closed. Measured lengths follow the same idea: null means the fact is missing, 0 means it was measured and is zero.
- **One writer per state field.** A stage returns only its own keys. It does not call the next stage. The graph is the only place that chooses the next node.
- **One revise.** The verifier sets `revision_count` to 1 when it wants another pass through the map tools. If it asks again, the graph sets the count to 2 and sends the request to the writer.
- **A second clarify is escalate.** The app keeps `clarify_count` between turns. When that count is already 1 or more, the graph relabels the action as `escalate` before the writer runs.
- **Failures stop the run.** A raised exception or a spent 30-second budget writes a handoff (`system_error` or `timeout`) and the system-error sentence, then ends. The log stores the exception type. It does not store the exception message or the raw request, because this can happen before the input guard has masked the text.
- **Place search is not tied to one country or city.** `maps.geocode_country` is null, so places resolve in any country. Set an ISO 3166 alpha-2 code, such as `ca`, only to restrict the search. No city's coordinates are built into the configuration.
- **`deadline` uses `time.monotonic()`.** The graph fills it in when the caller does not. Do not pass a `time.time()` value.
- **Stage modules are imported, not created here.** With `STUBS` unset, a missing stage module is an import error. `STUBS=1` is the way to run the flow before that module exists.
- **The pilot plans walking, running, cycling, and driving.** `walk`, `run`, `bike`, and `car` are accepted. A run is routed like a walk. A trip may also combine those four, for example cycling and then walking. A hike or a transit request is named by the classifier and turned away with a short reply. It is not relabelled as a walk or a drive. If the user names no mode, ask them to choose walking, running, cycling, or driving. `hike`, `transit`, and `multimodal` stay in the schema so an unsupported request can be recognized on purpose. `multimodal` is accepted only when every part is one of the four allowed modes.
