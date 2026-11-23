# Owner: Person A (WP7 app), Team Rosedale, MMAI 894.
# Only Person A edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP7.
# Written for this project with AI assistance; no code copied from other sources.
"""WP7 app test: T-7-08 runs the handbook example and shows the map."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from routechat.stubs import RUNNING_UTTERANCE

_APP = Path(__file__).resolve().parents[1] / "routechat" / "app.py"


def test_t_7_08_running_example_shows_the_map(tmp_path, monkeypatch):
    monkeypatch.setenv("STUBS", "1")
    monkeypatch.setenv("ROUTECHAT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("ROUTECHAT_HANDOFF_QUEUE", str(tmp_path / "handoff.jsonl"))
    app = AppTest.from_file(str(_APP), default_timeout=30)
    app.run()
    app.chat_input[0].set_value(RUNNING_UTTERANCE).run()
    visible = " ".join(block.value for block in app.markdown)
    assert "4.2 km" in visible
    assert "Canadian Tire" in visible
    assert "What I checked" in visible
    assert "Nothing is waiting" in visible
    assert "deck_gl_json_chart" in _element_types(app._tree)
    assert len(app.chat_message) == 2
    new_chat = next(button for button in app.button if button.label == "New chat")
    new_chat.click().run()
    assert app.chat_message == []
    assert any("New chat clears" in block.value for block in app.markdown)


def test_sponsor_pin_has_a_visible_label(tmp_path, monkeypatch):
    """P-05: a sponsor on the map is labelled on screen, not only in the hover tip, and is not a stop."""
    from routechat import stubs
    from routechat.schemas import SponsoredSuggestion

    sponsor = SponsoredSuggestion(
        name="Fowler Coffee House", category="cafe", lon=-79.3840, lat=43.6530, rating=4.3, distance_m=180
    )
    real_report = stubs.running_report
    monkeypatch.setattr(
        stubs, "running_report", lambda: real_report().model_copy(update={"sponsored": sponsor})
    )
    monkeypatch.setenv("STUBS", "1")
    monkeypatch.setenv("ROUTECHAT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("ROUTECHAT_HANDOFF_QUEUE", str(tmp_path / "handoff.jsonl"))
    app = AppTest.from_file(str(_APP), default_timeout=30)
    app.run()
    app.chat_input[0].set_value(RUNNING_UTTERANCE).run()

    deck = _deck_json(app._tree)
    text_layers = [layer for layer in deck["layers"] if layer["@@type"] == "TextLayer"]
    assert len(text_layers) == 1
    assert [row["label"] for row in text_layers[0]["data"]] == ["Sponsored"]
    legend = next(block.value for block in app.markdown if block.value.startswith("<div class='rc-legend'>"))
    assert "Sponsored" in legend
    places = next(block.value for block in app.markdown if ">Places<" in block.value)
    assert "Fowler Coffee House" not in places


def _catalog(tmp_path) -> str:
    import json

    records = [
        {"id": "sp_a", "name": "Cafe A", "category": "cafe", "rating": 4.3, "lat": 43.650, "lon": -79.380},
        {"id": "sp_b", "name": "Shop B", "category": "bike_shop", "rating": 3.2, "lat": 43.660, "lon": -79.390},
        {"id": "sp_c", "name": "No coordinates", "category": "cafe", "rating": 4.8},
    ]
    path = tmp_path / "sponsors.json"
    path.write_text(json.dumps(records))
    return str(path)


def _text_labels(app) -> list[str]:
    deck = _deck_json(app._tree)
    return [row["label"] for layer in deck.get("layers", []) if layer["@@type"] == "TextLayer" for row in layer["data"]]


def test_show_sponsors_toggle_draws_the_whole_catalog(tmp_path, monkeypatch):
    """Off by default; on, every catalog sponsor with coordinates gets a pin and a visible Sponsored tag."""
    monkeypatch.setenv("STUBS", "1")
    monkeypatch.setenv("ROUTECHAT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("ROUTECHAT_HANDOFF_QUEUE", str(tmp_path / "handoff.jsonl"))
    monkeypatch.setenv("ROUTECHAT_SPONSORS", _catalog(tmp_path))
    app = AppTest.from_file(str(_APP), default_timeout=30)
    app.run()
    toggle = app.toggle(key="show_sponsors")
    assert toggle.value is False and not toggle.disabled
    assert _text_labels(app) == []

    toggle.set_value(True).run()
    assert _text_labels(app) == ["Sponsored", "Sponsored"]

    app.chat_input[0].set_value(RUNNING_UTTERANCE).run()
    assert _text_labels(app) == ["Sponsored", "Sponsored"]
    places = next(block.value for block in app.markdown if ">Places<" in block.value)
    assert "Cafe A" not in places


def test_show_sponsors_is_disabled_without_a_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("STUBS", "1")
    monkeypatch.setenv("ROUTECHAT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("ROUTECHAT_HANDOFF_QUEUE", str(tmp_path / "handoff.jsonl"))
    monkeypatch.setenv("ROUTECHAT_SPONSORS", str(tmp_path / "missing.json"))
    app = AppTest.from_file(str(_APP), default_timeout=30)
    app.run()
    assert app.toggle(key="show_sponsors").disabled
    assert _text_labels(app) == []


def _deck_json(node) -> dict:
    import json

    proto = getattr(node, "proto", None)
    if getattr(node, "type", None) == "deck_gl_json_chart" and proto is not None:
        return json.loads(proto.json)
    children = getattr(node, "children", None)
    if isinstance(children, dict):
        for child in children.values():
            found = _deck_json(child)
            if found:
                return found
    return {}


def _element_types(node) -> list[str]:
    found = []
    element_type = getattr(node, "type", None)
    if element_type:
        found.append(element_type)
    children = getattr(node, "children", None)
    if isinstance(children, dict):
        for child in children.values():
            found.extend(_element_types(child))
    return found


# --- Turns: what the app sends to the graph, and what it keeps ---


def _fake_invoke(monkeypatch, actions: list[str]) -> list[dict]:
    """Record each payload. Reply with these classifier actions in order, and no route."""
    from routechat import graph
    from routechat.schemas import FinalAnswer, RouteClassification
    from routechat.stubs import running_constraints

    sent: list[dict] = []

    def invoke(payload):
        action = actions[len(sent)]
        sent.append(payload)
        state = {
            **payload,
            "classification": RouteClassification(
                route_action=action,
                primary_mode="bike",
                needs_live_data=False,
                clarify_field="origin" if action == "clarify" else "none",
                refusal_reason="booking_or_payment" if action == "refuse" else "none",
            ),
            "final": FinalAnswer(
                approved=True, text="ok", route_geojson=None, stops=[], used_fallback_template=False
            ),
        }
        if action in {"plan", "clarify"}:
            state["constraints"] = running_constraints()
        return state

    monkeypatch.setattr(graph, "invoke", invoke)
    return sent


def _run_turns(monkeypatch, actions: list[str], messages: list[str]):
    sent = _fake_invoke(monkeypatch, actions)
    monkeypatch.setenv("STUBS", "1")
    app = AppTest.from_file(str(_APP), default_timeout=30)
    app.run()
    for message in messages:
        app.chat_input[0].set_value(message).run()
    assert not app.exception
    return app, sent


def test_a_follow_up_answer_is_sent_with_the_message_it_answers(monkeypatch):
    app, sent = _run_turns(
        monkeypatch, ["clarify", "plan", "plan"], ["bike to the ROM please.", "From Union Station", "skip the stop"]
    )
    assert [payload["utterance"] for payload in sent] == [
        "bike to the ROM please.",
        "bike to the ROM please. From Union Station",
        "skip the stop",
    ]
    # The half-read trip on a clarify is not kept as the known trip.
    assert sent[1]["known"] is None and sent[1]["turn_index"] == 0
    # The thread shows what the user typed, not the joined text.
    assert [m.markdown[0].value for m in app.chat_message][2] == "From Union Station"


def test_an_answer_that_would_pass_the_length_limit_goes_alone(monkeypatch):
    _, sent = _run_turns(monkeypatch, ["clarify", "plan"], ["x" * 1990, "From Union Station"])
    assert sent[1]["utterance"] == "From Union Station"


def test_turn_index_follows_the_known_trip_and_never_passes_one(monkeypatch):
    from routechat.schemas import UserTurn

    _, sent = _run_turns(
        monkeypatch, ["refuse", "refuse", "plan", "plan", "plan"], ["one", "two", "three", "four", "five"]
    )
    assert [payload["turn_index"] for payload in sent] == [0, 0, 0, 1, 1]
    for payload in sent:
        UserTurn(utterance="x", turn_index=payload["turn_index"], known=payload["known"])


def test_an_escalated_trip_opens_a_ticket_and_the_next_trip_starts_fresh(monkeypatch):
    from routechat.logging import read_handoffs

    app, sent = _run_turns(
        monkeypatch,
        ["clarify", "escalate", "clarify"],
        ["somewhere, mail ada@example.com", "there", "bike to the ROM"],
    )
    tickets = read_handoffs()
    assert [ticket["reason"] for ticket in tickets] == ["still_unclear"]
    assert "ada@example.com" not in tickets[0]["redacted_request"]
    assert "there" in tickets[0]["redacted_request"]
    assert sent[2]["utterance"] == "bike to the ROM"
    assert sent[2]["clarify_count"] == 0
    assert "Nothing is waiting" not in " ".join(block.value for block in app.markdown)


def test_the_map_stays_when_a_reply_has_no_route(monkeypatch):
    app, _ = _run_turns(monkeypatch, ["refuse"], ["book a hotel"])
    assert "deck_gl_json_chart" in _element_types(app._tree)


# --- Settings the app fills in ---


def _started_with(monkeypatch, tmp_path, dotenv: str) -> AppTest:
    path = tmp_path / ".env"
    path.write_text(dotenv, encoding="utf-8")
    monkeypatch.setenv("ROUTECHAT_DOTENV", str(path))
    # setenv first, so monkeypatch restores whatever the app sets; load_dotenv skips a name that exists.
    for name in ("STUBS", "FIXTURES", "OPENROUTER_API_KEY", "CLASSIFIER_BACKEND"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.setenv("CLASSIFIER_ADAPTER_DIR", str(tmp_path / "no_adapter"))
    app = AppTest.from_file(str(_APP), default_timeout=30)
    app.run()
    assert not app.exception
    return app


def test_a_key_in_dotenv_runs_the_real_stages(tmp_path, monkeypatch):
    import os

    _started_with(monkeypatch, tmp_path, "OPENROUTER_API_KEY=test-value\nSTUBS=\nCLASSIFIER_BACKEND=\n")
    assert os.environ["STUBS"] == "0"
    assert os.environ["CLASSIFIER_BACKEND"] == "prompted_small"


def test_no_key_shows_the_sample_trip(tmp_path, monkeypatch):
    import os

    app = _started_with(monkeypatch, tmp_path, "OPENROUTER_API_KEY=\n")
    assert os.environ["STUBS"] == "1"
    assert any("Sample trip" in block.value for block in app.markdown)


def test_an_explicit_stubs_setting_wins_over_a_key(tmp_path, monkeypatch):
    import os

    _started_with(monkeypatch, tmp_path, "OPENROUTER_API_KEY=test-value\nSTUBS=1\n")
    assert os.environ["STUBS"] == "1"
