# RouteChatbot

Conversational route planner for MMAI 894. A request passes through seven stages. LangGraph runs them in order and chooses every branch in Python. Only the classifier, the constraint extractor, and the reply writer call a model.

This repository currently contains WP0: the shared contracts, the configuration, and the orchestrator. Stage packages plug in beside it. They are not part of this package.

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

Until that package is merged, the orchestrator checks are:

```bash
FIXTURES=replay uv run pytest
```

`FIXTURES=replay` reads recorded API responses, so those tests do not call Mapbox or GraphHopper and do not spend credits.

## Layout

```text
config.yaml              thresholds, model names, prices
routechat/config.py      typed access to config.yaml
routechat/schemas.py     shared contracts, schemas v1
routechat/graph.py       stage order and every branch
routechat/stubs.py       running-example output for STUBS=1
tests/test_graph.py      T-0-01 to T-0-05
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
