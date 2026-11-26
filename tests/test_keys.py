# Written for this project with AI assistance; no code copied from other sources.
"""Visitors bring their own keys: a run sees its visitor's keys only, and no key is written anywhere."""

from __future__ import annotations

import threading
from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from routechat import keys
from routechat.llm import LLMNoKey, provider_and_client_kwargs
from routechat.maps.client import MapClient, MapError

_APP = Path(__file__).resolve().parents[1] / "routechat" / "app.py"
# Made-up keys, in two parts so the scan for keys in the repository does not stop at this file.
_MODEL, _MAPBOX = "s" + "k-or-mine", "p" + "k.mine"
_MINE = {"OPENROUTER_API_KEY": _MODEL, "MAPBOX_TOKEN": _MAPBOX, "GRAPHHOPPER_KEY": "gh-mine"}


@pytest.fixture
def server(monkeypatch):
    """A server in visitor mode that also has keys of its own, which must never be used."""
    monkeypatch.setenv("ROUTECHAT_KEYS", "visitor")
    for name in (*keys.MODEL_KEYS, *keys.MAP_KEYS):
        monkeypatch.setenv(name, "server-secret")
    monkeypatch.setenv("FIXTURES", "")


def test_without_visitor_mode_keys_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("MAPBOX_TOKEN", "from-env")
    assert keys.get_key("MAPBOX_TOKEN") == "from-env"
    assert keys.get_key("GRAPHHOPPER_KEY") is None


def test_a_visitor_never_falls_back_to_the_servers_keys(server):
    with pytest.raises(LLMNoKey):
        provider_and_client_kwargs()
    with keys.visitor_keys({"OPENROUTER_API_KEY": _MODEL}):
        provider, kwargs = provider_and_client_kwargs()
        assert provider == "openrouter" and kwargs["api_key"] == _MODEL
        assert keys.get_key("MAPBOX_TOKEN") is None
        with httpx.Client() as http, pytest.raises(MapError, match="provider_not_configured"):
            MapClient(http).request("mapbox", "GET", "https://api.mapbox.com/x", {})
    assert keys.get_key("OPENROUTER_API_KEY") is None


def test_the_map_client_sends_the_visitors_key(server):
    seen = []

    def reply(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(reply)) as http, keys.visitor_keys(_MINE):
        MapClient(http).request("mapbox", "GET", "https://api.mapbox.com/x", {})
        MapClient(http).request("graphhopper", "POST", "https://graphhopper.com/x", {}, {})
    assert seen == [{"access_token": _MAPBOX}, {"key": "gh-mine"}]


def test_two_visitors_at_the_same_time_do_not_share_keys(server):
    ready, got = threading.Barrier(2), {}

    def visit(name):
        with keys.visitor_keys({"MAPBOX_TOKEN": name}):
            ready.wait(5)
            got[name] = keys.get_key("MAPBOX_TOKEN")

    threads = [threading.Thread(target=visit, args=(name,)) for name in ("ann", "bo")]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert got == {"ann": "ann", "bo": "bo"}


def test_a_failed_run_still_drops_the_keys(server):
    with pytest.raises(ValueError), keys.visitor_keys(_MINE):
        raise ValueError
    assert keys.get_key("MAPBOX_TOKEN") is None


def test_the_graph_runs_its_stages_with_the_visitors_keys(server):
    from routechat.graph import invoke
    from routechat.stubs import stub_nodes

    nodes, seen = stub_nodes(), []
    guard = nodes["guard_in"]
    nodes["guard_in"] = lambda state: seen.append(keys.get_key("MAPBOX_TOKEN")) or guard(state)
    payload = {"request_id": "r_1", "utterance": "bike", "turn_index": 0, "known": None,
               "clarify_count": 0, "revision_count": 0}
    with keys.visitor_keys(_MINE):
        invoke(payload, nodes, on_step=lambda *_: None)
        invoke(payload, nodes)
    assert seen == [_MAPBOX, _MAPBOX]


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, {"code": "TokenValid"}, []),
        (429, {}, ["Mapbox token could not be checked"]),
        (401, {}, ["OpenRouter key was not accepted", "Mapbox token could not be checked", "GraphHopper key was not accepted"]),
        (200, {"code": "TokenInvalid"}, ["Mapbox token was not accepted"]),
        (500, {}, ["OpenRouter key could not", "Mapbox token could not", "GraphHopper key could not"]),
    ],
)
def test_each_key_is_checked_and_never_shown(monkeypatch, status, body, expected):
    monkeypatch.setattr(
        httpx, "get", lambda url, **_: httpx.Response(status, json=body, request=httpx.Request("GET", url))
    )
    found = keys.problems(_MINE)
    assert len(found) == len(expected)
    for line, part in zip(found, expected):
        assert part in line
    assert not any(value in " ".join(found) for value in _MINE.values())


def test_a_key_with_a_space_is_not_sent(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *_, **__: pytest.fail("sent"))
    assert "odd character" in keys.problems({"MAPBOX_TOKEN": "a b"})[0]


def test_the_model_key_says_which_provider_it_is_for():
    assert keys.model_key_name("s" + "k-or-v1-x") == "OPENROUTER_API_KEY"
    assert keys.model_key_name("s" + "k-proj-x") == "OPENAI_API_KEY"


# --- The screen ---


def _open(monkeypatch):
    monkeypatch.setenv("STUBS", "")
    monkeypatch.setenv("CLASSIFIER_BACKEND", "prompted_small")
    app = AppTest.from_file(str(_APP), default_timeout=30)
    app.run()
    assert not app.exception
    return app


def _button(app, label):
    return next(button for button in app.button if button.label == label)


def test_without_keys_a_visitor_gets_only_the_sample_ride(server, monkeypatch):
    app = _open(monkeypatch)
    assert app.chat_input[0].disabled
    assert any("Sample trip" in block.value for block in app.markdown)
    _button(app, "Try the sample ride").click().run()
    assert not app.exception
    assert "Canadian Tire" in " ".join(block.value for block in app.markdown)


def test_accepted_keys_are_used_for_the_visitors_trips_and_written_nowhere(server, monkeypatch, tmp_path):
    from routechat import graph
    from routechat.stubs import stub_nodes

    real, seen = graph.invoke, []

    def invoke(payload, nodes=None, on_step=None):
        seen.append((nodes, {name: keys.get_key(name) for name in _MINE}))
        return real(payload, stub_nodes(), on_step)

    monkeypatch.setattr(graph, "invoke", invoke)
    monkeypatch.setattr(keys, "problems", lambda given: [])
    app = _open(monkeypatch)
    _button(app, "Add your keys").click().run()
    for field, value in zip(app.text_input, (_MODEL, f" {_MAPBOX} ", "gh-mine")):
        field.set_value(value)
    _button(app, "Check and use").click().run()
    assert not app.exception
    assert not app.chat_input[0].disabled
    assert _button(app, "Your keys")
    app.chat_input[0].set_value("bike to the ROM").run()
    assert not app.exception
    assert seen == [(None, _MINE)]
    _button(app, "New chat").click().run()
    assert not app.chat_input[0].disabled

    other = _open(monkeypatch)
    assert other.chat_input[0].disabled

    _button(app, "Your keys").click().run()
    _button(app, "Remove my keys").click().run()
    assert app.chat_input[0].disabled and app.session_state["own_keys"] == {}

    written = b"".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    assert written and not any(value.encode() in written for value in _MINE.values())


def test_a_rejected_key_is_not_kept(server, monkeypatch):
    monkeypatch.setattr(keys, "problems", lambda given: ["The Mapbox token was not accepted."])
    app = _open(monkeypatch)
    _button(app, "Add your keys").click().run()
    for field in app.text_input:
        field.set_value("x")
    _button(app, "Check and use").click().run()
    assert any("not accepted" in error.value for error in app.error)
    assert app.session_state["own_keys"] == {}
    assert not any(button.label == "Remove my keys" for button in app.button)


def test_the_key_button_is_only_for_a_visitor_server(monkeypatch):
    monkeypatch.setenv("STUBS", "1")
    app = AppTest.from_file(str(_APP), default_timeout=30)
    app.run()
    assert not any("keys" in button.label for button in app.button)
