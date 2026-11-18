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
