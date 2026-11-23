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
