# Owner: Person A (WP7 app), Team Rosedale, MMAI 894.
# Only Person A edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP7.
# Written for this project with AI assistance; no code copied from other sources.
"""WP7 Streamlit app. Conversation on the left, the trip on the right.

The visual language follows EyesRhythm: a light paper background, Inter-like
type, soft white cards, and one blue. The map is a street basemap with the
route line and the place pins drawn on top. Places and checks sit under the
map. STUBS=1 is the default until the other stage packages are merged, so
this screen can run on its own.
"""

from __future__ import annotations

import os
from html import escape
from pathlib import Path
from typing import Any
from uuid import uuid4

os.environ.setdefault("STUBS", "1")

import orjson
import pydeck as pdk
import streamlit as st

from routechat.graph import invoke
from routechat.logging import append_handoff, log_event, mask_for_log, read_handoffs
from routechat.stubs import RUNNING_UTTERANCE

_ROUTE = [47, 120, 196]
_ROUTE_CASING = [255, 255, 255]
_START = [47, 120, 196, 230]
_STOP = [224, 161, 90, 235]
_END = [224, 122, 114, 230]
_SPONSOR = [91, 159, 224, 220]
_SPONSOR_INK = [31, 94, 158, 255]
SPONSOR_LABEL = "Sponsored"

_CSS = """
<style>
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap");

:root {
  --bg: #eef2f6;
  --card: #ffffff;
  --inset: #f6f8fb;
  --stroke: rgba(30, 48, 72, 0.10);
  --stroke-strong: rgba(30, 48, 72, 0.16);
  --text: #1e2c3a;
  --soft: #5a6f82;
  --faint: #8a9aab;
  --blue: #5b9fe0;
  --blue-deep: #2f78c4;
  --blue-ink: #1f5e9e;
  --peach: #e8b896;
  --amber: #e0a15a;
  --rose: #e07a72;
  --shadow: 0 12px 32px rgba(30, 48, 72, 0.07);
  --shadow-soft: 0 1px 2px rgba(30, 48, 72, 0.05);
  --rail: 380px;
  --radius: 18px;
  --control: 12px;
  --font: Inter, "SF Pro Text", "Segoe UI", system-ui, sans-serif;
}

html, body, [data-testid="stAppViewContainer"], .stApp {
  background:
    radial-gradient(900px 280px at 0% -10%, rgba(91, 159, 224, 0.16), transparent 55%),
    var(--bg);
  color: var(--text);
  font-family: var(--font);
}
[data-testid="stHeader"] { background: transparent; height: 0; }
#MainMenu, footer, [data-testid="stDecoration"] { display: none !important; }
[data-testid="stMain"], [data-testid="stAppViewContainer"] { overflow: hidden; }
.block-container {
  padding: 14px 16px 12px !important;
  max-width: 100% !important;
}
button, input, textarea, [data-testid="stMarkdown"], [data-testid="stMarkdownContainer"] p {
  font-family: var(--font);
}

.st-key-topbar {
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid var(--stroke);
  border-radius: 16px;
  box-shadow: var(--shadow-soft);
  padding: 12px 16px !important;
  margin-bottom: 12px;
  justify-content: center !important;
}
.st-key-topbar [data-testid="stHorizontalBlock"] { align-items: center !important; width: 100%; }
.st-key-topbar [data-testid="stColumn"],
.st-key-topbar [data-testid="stElementContainer"],
.st-key-topbar [data-testid="stMarkdown"],
.st-key-topbar [data-testid="stMarkdownContainer"] {
  display: flex !important;
  align-items: center !important;
  margin: 0 !important;
  padding: 0 !important;
}
.st-key-topbar [data-testid="stMarkdownContainer"] p { margin: 0 !important; }
.st-key-topbar [data-testid="stVerticalBlock"] { gap: 0 !important; }
@media (min-width: 1100px) {
  .st-key-topbar [data-testid="stColumn"]:last-child {
    flex: 0 0 156px !important;
    width: 156px !important;
    min-width: 156px !important;
    max-width: 156px !important;
    justify-content: flex-end !important;
  }
}

.rc-brand { display: flex; align-items: center; gap: 12px; min-height: 44px; }
.rc-mark {
  width: 36px; height: 36px; border-radius: 11px; flex: none;
  display: grid; place-items: center;
  background: linear-gradient(160deg, #74b4ea 0%, #2f78c4 100%);
  box-shadow: 0 6px 14px rgba(47, 120, 196, 0.28);
  line-height: 0;
}
.rc-mark svg { display: block; }
.rc-brand-copy {
  display: flex; flex-direction: column; justify-content: center;
  gap: 2px; min-width: 0;
}
.rc-brand-copy h1 {
  font-size: 18px !important; font-weight: 650 !important; letter-spacing: -0.03em;
  margin: 0 !important; padding: 0 !important; line-height: 1.25 !important; color: var(--text);
}
.rc-brand-copy p {
  margin: 0 !important; padding: 0 !important; color: var(--soft);
  font-size: 12.5px !important; line-height: 1.35 !important; white-space: nowrap;
}
.rc-pill {
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 11px; line-height: 1; color: var(--blue-ink); background: #e7f1fb;
  border: 1px solid rgba(47, 120, 196, 0.16);
  border-radius: 999px; padding: 6px 10px; font-weight: 650;
  letter-spacing: 0.02em; align-self: center;
}

.st-key-topbar [data-testid="stBaseButton-secondary"],
.st-key-rail [data-testid="stBaseButton-primary"] {
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  gap: 8px !important;
  line-height: 1 !important;
  padding: 0 16px !important;
}
.st-key-topbar [data-testid="stBaseButton-secondary"] {
  border-radius: var(--control) !important;
  border: 1px solid var(--stroke-strong) !important;
  background: #fff !important;
  color: var(--text) !important;
  font-weight: 600 !important;
  font-size: 13.5px !important;
  min-height: 40px !important;
  height: 40px !important;
  box-shadow: var(--shadow-soft) !important;
}
.st-key-topbar [data-testid="stBaseButton-secondary"]:hover {
  border-color: #8ebfed !important;
  color: var(--blue-deep) !important;
  background: #f7fbff !important;
}
.st-key-topbar [data-testid="stBaseButton-secondary"] p,
.st-key-rail [data-testid="stBaseButton-primary"] p {
  margin: 0 !important;
  padding: 0 !important;
  line-height: 1 !important;
  font-weight: 600;
}

@media (min-width: 1100px) {
  [data-testid="stColumn"]:has(.st-key-rail) {
    flex: 0 0 var(--rail) !important;
    width: var(--rail) !important;
    min-width: var(--rail) !important;
    max-width: var(--rail) !important;
  }
  [data-testid="stColumn"]:has(.st-key-stage) {
    flex: 1 1 auto !important;
    width: auto !important;
    min-width: 0 !important;
  }
}

.st-key-rail, .st-key-stage {
  background: var(--card);
  border: 1px solid var(--stroke);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  /* Streamlit's column flex-basis ignores a plain height, so pin all three. */
  flex: 0 0 auto !important;
  height: calc(100vh - 132px) !important;
  min-height: calc(100vh - 132px) !important;
  max-height: calc(100vh - 132px) !important;
  overflow: hidden !important;
  display: flex !important;
  flex-direction: column !important;
  gap: 8px !important;
}
.st-key-rail { padding: 16px !important; }
.st-key-stage { padding: 16px !important; }
.st-key-rail [data-testid="stElementContainer"],
.st-key-stage [data-testid="stElementContainer"],
.st-key-rail [data-testid="stMarkdown"],
.st-key-stage [data-testid="stMarkdown"],
.st-key-rail [data-testid="stMarkdownContainer"],
.st-key-stage [data-testid="stMarkdownContainer"] { margin: 0 !important; }
.st-key-rail [data-testid="stMarkdownContainer"] p,
.st-key-stage [data-testid="stMarkdownContainer"] p { margin-bottom: 0; }
.st-key-rail > div { flex: 0 0 auto !important; }
.st-key-rail > div:has(.st-key-thread) {
  flex: 1 1 auto !important;
  min-height: 0 !important;
  overflow: hidden !important;
  display: flex !important;
  flex-direction: column !important;
}
.st-key-thread {
  flex: 1 1 auto !important;
  min-height: 0 !important;
  overflow: auto !important;
  padding-right: 2px;
  scrollbar-width: thin;
  scrollbar-color: rgba(30, 48, 72, 0.28) transparent;
  gap: 4px !important;
}
.st-key-rail > div:has([data-testid="stChatInput"]) {
  flex: 0 0 auto !important;
}

.rc-panel-title {
  font-size: 13px; font-weight: 650; letter-spacing: -0.01em;
  color: var(--text); margin: 2px 2px 10px;
}
.rc-empty { padding: 8px 4px 4px; }
.rc-empty h2 {
  margin: 4px 0 6px !important; font-size: 22px !important; letter-spacing: -0.03em;
  font-weight: 650 !important; line-height: 1.2 !important; padding: 0 !important;
}
.rc-empty p, .rc-support p, .rc-caption {
  color: var(--soft); font-size: 13.5px; line-height: 1.45; margin: 0;
}
.rc-kicker, .rc-section, .rc-field-label {
  color: var(--faint);
  font-size: 11px !important;
  font-weight: 700 !important;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  margin: 0 !important;
  line-height: 1.4 !important;
  padding: 0 !important;
}
.rc-field-label { margin: 10px 2px 8px !important; }
.rc-hint { color: var(--faint); font-size: 12.5px; margin: 8px 0 0; line-height: 1.4; }

.st-key-rail [data-testid="stBaseButton-primary"] {
  border-radius: var(--control) !important;
  border: 1px solid #286aaf !important;
  background: linear-gradient(180deg, #3d86cc 0%, #2f78c4 100%) !important;
  color: #fff !important;
  font-weight: 600 !important;
  font-size: 14px !important;
  min-height: 44px !important;
  height: 44px !important;
  box-shadow: 0 8px 16px rgba(47, 120, 196, 0.24) !important;
  margin-top: 14px;
}
.st-key-rail [data-testid="stBaseButton-primary"]:hover {
  background: #286aaf !important;
}
.st-key-rail [data-testid="stBaseButton-primary"] p { color: #fff !important; }
button:focus-visible {
  outline: none !important;
  box-shadow: 0 0 0 3px rgba(91, 159, 224, 0.35) !important;
}

[data-testid="stChatInput"] {
  border-radius: 14px !important;
  background: var(--inset) !important;
}
[data-testid="stChatInput"] > div {
  border-radius: 14px !important;
  border: 1px solid var(--stroke-strong) !important;
  background: var(--inset) !important;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7);
  align-items: center !important;
  min-height: 48px !important;
  padding: 8px 10px 8px 14px !important;
}
[data-testid="stChatInput"] > div:focus-within {
  border-color: var(--blue) !important;
  background: #fff !important;
  box-shadow: 0 0 0 3px rgba(91, 159, 224, 0.22) !important;
}
[data-testid="stChatInputTextArea"] {
  font-size: 14.5px !important;
  line-height: 1.4 !important;
  color: var(--text) !important;
  caret-color: var(--blue-deep);
  padding: 6px 2px !important;
  margin: 0 !important;
}
[data-testid="stChatInputTextArea"]::placeholder { color: var(--faint); }
[data-testid="stChatInputSubmitButton"] {
  background: var(--blue-deep) !important;
  color: #fff !important;
  border-radius: 10px !important;
}
[data-testid="stChatInputInstructions"] { color: var(--faint) !important; font-size: 11px !important; }

[data-testid="stChatMessage"] {
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  padding: 0 0 8px !important;
  gap: 8px !important;
}
[data-testid="stChatMessageAvatarUser"],
[data-testid="stChatMessageAvatarAssistant"] {
  display: none !important;
}
[data-testid="stChatMessageContent"] {
  border-radius: 4px 14px 14px 14px;
  padding: 12px 14px !important;
  border: 1px solid var(--stroke);
  background: #fff;
  height: auto !important;
  box-sizing: border-box !important;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {
  background: #eef5fc;
  border-color: rgba(47, 120, 196, 0.16);
}
[data-testid="stChatMessageContent"] p,
[data-testid="stChatMessageContent"] [data-testid="stMarkdown"],
[data-testid="stChatMessageContent"] [data-testid="stMarkdownContainer"] {
  margin: 0 !important;
  padding: 0 !important;
  font-size: 14px;
  line-height: 1.45 !important;
  color: var(--text);
  height: auto !important;
}

.rc-support {
  background: var(--inset);
  border: 1px solid rgba(30, 48, 72, 0.07);
  border-radius: 14px;
  padding: 12px 16px;
  max-height: 128px;
  overflow: auto;
}
.rc-support strong { display: block; margin-top: 4px; font-size: 14px; letter-spacing: -0.02em; }
.rc-support .rc-ticket + .rc-ticket {
  margin-top: 8px; padding-top: 8px;
  border-top: 1px solid rgba(30, 48, 72, 0.07);
}
.rc-support .rc-ticket b { font-size: 13.5px; }
.rc-support .rc-ticket p { margin-top: 2px; }

.rc-metrics {
  display: grid;
  gap: 0;
  margin: 0 0 10px;
  background: var(--inset);
  border: 1px solid rgba(30, 48, 72, 0.08);
  border-radius: 14px;
  overflow: hidden;
}
.rc-metric {
  display: flex; flex-direction: column; justify-content: center; gap: 4px;
  padding: 12px 16px; border-right: 1px solid rgba(30, 48, 72, 0.08); background: transparent;
  min-height: 64px; box-sizing: border-box;
}
.rc-metric:last-child { border-right: none; }
.rc-metric span {
  display: block; color: var(--faint); font-size: 10.5px; font-weight: 700;
  letter-spacing: 0.05em; text-transform: uppercase; line-height: 1.3 !important; margin: 0;
}
.rc-metric strong {
  display: block; margin: 0; font-size: 16px; letter-spacing: -0.03em;
  color: var(--text); font-weight: 650; line-height: 1.2 !important;
}

.st-key-stage > div:has(.rc-metrics),
.st-key-stage > div:has(.rc-legend),
.st-key-stage > div:has(.rc-caption) { flex: 0 0 auto !important; }
.st-key-stage > div:has([data-testid="stDeckGlJsonChart"]) {
  flex: 1 1 auto !important;
  min-height: 150px !important;
  overflow: hidden !important;
  display: flex !important;
  flex-direction: column !important;
}
.st-key-stage > div:has(.rc-legend) { margin: 8px 0 8px !important; }
.st-key-stage > div:has(.st-key-facts) {
  flex: 0 0 auto !important;
  max-height: none !important;
  overflow: visible !important;
}
[data-testid="stDeckGlJsonChart"],
[data-testid="stPydeckChart"] {
  border-radius: 14px !important;
  overflow: hidden !important;
  border: 1px solid rgba(30, 48, 72, 0.08);
  flex: 1 1 auto !important;
  height: 100% !important;
  min-height: 150px !important;
}
[data-testid="stDeckGlJsonChart"] iframe,
[data-testid="stPydeckChart"] iframe {
  flex: 1 1 auto !important;
  height: 100% !important;
  min-height: 150px !important;
}
.st-key-stage:not(:has(.st-key-facts)) [data-testid="stDeckGlJsonChart"],
.st-key-stage:not(:has(.st-key-facts)) [data-testid="stPydeckChart"],
.st-key-stage:not(:has(.st-key-facts)) [data-testid="stDeckGlJsonChart"] iframe,
.st-key-stage:not(:has(.st-key-facts)) [data-testid="stPydeckChart"] iframe {
  height: calc(100vh - 248px) !important;
  min-height: 320px !important;
}
.rc-legend {
  display: flex; gap: 16px; align-items: center;
  color: var(--soft); font-size: 12.5px; margin: 0 2px;
  padding: 2px 2px 6px;
}
.rc-legend i {
  display: inline-block; width: 8px; height: 8px; border-radius: 99px;
  margin-right: 6px; vertical-align: 1px;
}
.rc-legend i.start { background: #2f78c4; }
.rc-legend i.stop { background: #e0a15a; }
.rc-legend i.end { background: #e07a72; }
.rc-legend i.sponsored { background: #5b9fe0; }
.rc-caption { margin: 8px 2px 0; }

.st-key-facts { margin: 0 !important; min-height: 0; overflow: visible; gap: 12px !important; }
.rc-panel {
  background: var(--inset);
  border: 1px solid rgba(30, 48, 72, 0.07);
  border-radius: 14px;
  padding: 12px 16px;
}
.rc-section { display: block; margin: 0 0 4px !important; }
.rc-row {
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  padding: 7px 0;
  border-top: 1px solid rgba(30, 48, 72, 0.06);
}
.rc-row b { font-size: 13.5px !important; line-height: 1.25 !important; }
.rc-row p { font-size: 12px !important; line-height: 1.3 !important; margin-top: 1px !important; }
.rc-row:first-child { border-top: none; }
.rc-pip { width: 8px; height: 8px; border-radius: 99px; margin-top: 0; justify-self: center; }
.rc-pip.origin, .rc-pip.met { background: var(--blue-deep); }
.rc-pip.stop { background: var(--amber); }
.rc-pip.destination { background: var(--rose); }
.rc-pip.sponsored, .rc-pip.unverifiable { background: var(--blue); }
.rc-pip.unmet { background: var(--rose); }
.rc-row b { display: block; font-size: 14px; letter-spacing: -0.02em; line-height: 1.3; }
.rc-row p { margin: 2px 0 0; color: var(--soft); font-size: 12.5px; line-height: 1.4; }
.rc-badge {
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700; letter-spacing: 0.03em; line-height: 1;
  text-transform: uppercase; border-radius: 999px; padding: 6px 10px;
  background: #fff; border: 1px solid var(--stroke); color: var(--soft);
  white-space: nowrap; align-self: center;
}
.rc-badge.met { color: var(--blue-ink); background: #e7f1fb; border-color: rgba(47, 120, 196, 0.16); }
.rc-badge.unmet { color: #9c403c; background: #fdeceb; border-color: rgba(224, 122, 114, 0.3); }
.rc-badge.unverifiable { color: #8a5a1e; background: #fbf3e7; border-color: rgba(224, 161, 90, 0.35); }
</style>
"""


def _field(value: Any, name: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _init() -> None:
    st.session_state.setdefault("history", [])
    st.session_state.setdefault("known", None)
    st.session_state.setdefault("clarify_count", 0)
    st.session_state.setdefault("turn_index", 0)
    st.session_state.setdefault("last", None)
    st.session_state.setdefault("composer_epoch", 0)


def _new_chat() -> None:
    """Drop this thread. The next message starts from a blank map."""
    st.session_state.history = []
    st.session_state.known = None
    st.session_state.clarify_count = 0
    st.session_state.turn_index = 0
    st.session_state.last = None
    st.session_state.composer_epoch = int(st.session_state.get("composer_epoch", 0)) + 1


def _mode_name(mode: str | None) -> str:
    return {
        "walk": "Walking",
        "run": "Running",
        "hike": "Hiking",
        "bike": "Cycling",
        "transit": "Transit",
        "car": "Driving",
    }.get(mode or "", "Trip")


def _minutes(candidate: Any) -> int | None:
    traffic = _field(candidate, "duration_traffic_s")
    seconds = traffic if traffic is not None else _field(candidate, "duration_s")
    if seconds is None:
        return None
    return int(round(seconds / 60))


def _ask(utterance: str) -> None:
    """Run one turn and remember the reply, the constraints, and the map state."""
    request_id = f"r_{uuid4().hex[:8]}"
    state = invoke(
        {
            "request_id": request_id,
            "utterance": utterance,
            "turn_index": st.session_state.turn_index,
            "known": st.session_state.known,
            "clarify_count": st.session_state.clarify_count,
            "revision_count": 0,
        }
    )
    final = state.get("final")
    answer = _field(final, "text", "I couldn't finish that trip. Please try again in a minute.")
    st.session_state.history.append({"role": "user", "content": utterance})
    st.session_state.history.append({"role": "assistant", "content": answer})
    st.session_state.turn_index += 1
    constraints = state.get("constraints")
    if constraints is not None:
        st.session_state.known = constraints
    action = _field(state.get("classification"), "route_action")
    if action == "clarify":
        st.session_state.clarify_count += 1
    elif action in {"plan", "replan", "compare_modes", "lookup_only"}:
        st.session_state.clarify_count = 0
    report = state.get("report")
    if _field(report, "decision") == "handoff" or state.get("handoff") is not None:
        ticket = state.get("handoff") or {
            "request_id": request_id,
            "reason": _field(report, "handoff_reason") or "handoff",
            "redacted_request": mask_for_log(utterance),
        }
        if hasattr(ticket, "model_dump"):
            ticket = ticket.model_dump()
        append_handoff(ticket)
    log_event(
        {
            "request_id": request_id,
            "stage": "app",
            "decision": "fallback" if _field(final, "used_fallback_template") else "ok",
            "text": utterance,
        }
    )
    st.session_state.last = state


def _view(coords: list[list[float]]) -> pdk.ViewState:
    lons = [point[0] for point in coords]
    lats = [point[1] for point in coords]
    span = max(max(lons) - min(lons), max(lats) - min(lats), 0.01)
    # One step wider than a square map, so both pins stay inside the short stage.
    zoom = 12 if span < 0.03 else 11 if span < 0.08 else 10
    return pdk.ViewState(
        longitude=sum(lons) / len(lons),
        latitude=sum(lats) / len(lats),
        zoom=zoom,
        pitch=0,
        bearing=0,
    )


def _pretty_id(raw: str) -> str:
    parts = str(raw).replace("_", " ").split(":")
    return " · ".join(part.strip().title() for part in parts if part.strip())


def _sponsor_label_layer(sponsors: list[Any]) -> pdk.Layer:
    """A visible "Sponsored" tag over each sponsor pin, so the label never depends on hovering (P-05)."""
    return pdk.Layer(
        "TextLayer",
        [
            {"coordinates": [_field(item, "lon"), _field(item, "lat")], "label": SPONSOR_LABEL}
            for item in sponsors
        ],
        get_position="coordinates",
        get_text="label",
        get_size=12,
        get_color=_SPONSOR_INK,
        get_pixel_offset=[0, -20],
        get_text_anchor="'middle'",
        get_alignment_baseline="'bottom'",
        font_family="'Inter, \"SF Pro Text\", \"Segoe UI\", system-ui, sans-serif'",
        font_weight=600,
        background=True,
        get_background_color=[255, 255, 255, 235],
        background_padding=[6, 3, 6, 3],
        get_border_color=_SPONSOR,
        get_border_width=1,
        pickable=False,
    )


def sponsors_path() -> Path:
    """The WP8 sponsor catalog (D4). ROUTECHAT_SPONSORS overrides it, for tests."""
    override = os.environ.get("ROUTECHAT_SPONSORS")
    return Path(override) if override else Path(__file__).resolve().parents[1] / "data" / "sponsors.json"


def load_sponsors() -> list[dict[str, Any]]:
    """Every catalog entry with usable coordinates. A missing or broken file gives an empty list."""
    try:
        records = orjson.loads(sponsors_path().read_bytes())
    except (OSError, orjson.JSONDecodeError):
        return []
    if not isinstance(records, list):
        return []
    return [
        record
        for record in records
        if isinstance(record, dict)
        and isinstance(record.get("lat"), (int, float))
        and isinstance(record.get("lon"), (int, float))
    ]


def _catalog_layers(sponsors: list[dict[str, Any]]) -> list[pdk.Layer]:
    """The browse view: every catalog sponsor, each pin tagged "Sponsored" on screen (P-05)."""
    points = [
        {
            "coordinates": [record["lon"], record["lat"]],
            "tip": " · ".join(
                str(part)
                for part in (
                    SPONSOR_LABEL,
                    record.get("name"),
                    str(record.get("category") or "").replace("_", " "),
                    f"rating {record['rating']}" if record.get("rating") is not None else None,
                )
                if part
            ),
            "color": _SPONSOR,
        }
        for record in sponsors
    ]
    if not points:
        return []
    return [
        pdk.Layer(
            "ScatterplotLayer",
            points,
            get_position="coordinates",
            get_fill_color="color",
            get_line_color=[255, 255, 255],
            stroked=True,
            line_width_min_pixels=2,
            get_radius=60,
            radius_min_pixels=6,
            pickable=True,
        ),
        _sponsor_label_layer(sponsors),
    ]


def _sponsor_toggle() -> bool:
    available = bool(load_sponsors())
    shown = st.toggle(
        "Show sponsors",
        key="show_sponsors",
        disabled=not available,
        help="Every sponsor in the catalog, each labelled Sponsored."
        if available
        else "No sponsor catalog found at data/sponsors.json.",
    )
    return bool(shown and available)


def _legend_html(has_sponsor: bool) -> str:
    sponsor = "<span><i class='sponsored'></i>Sponsored</span>" if has_sponsor else ""
    return (
        "<div class='rc-legend'>"
        "<span><i class='start'></i>Start</span>"
        "<span><i class='stop'></i>Stop</span>"
        "<span><i class='end'></i>End</span>"
        f"{sponsor}"
        "</div>"
    )


def _map(state: dict[str, Any]) -> None:
    final = state.get("final")
    geometry = _field(final, "route_geojson")
    coords = [list(point[:2]) for point in (_field(geometry, "coordinates") or [])]
    places = list(state.get("places") or [])
    points: list[dict[str, Any]] = []
    for place in places:
        role = _field(place, "role")
        color = {"origin": _START, "stop": _STOP, "destination": _END}.get(role, _START)
        hours = _field(place, "opening_hours")
        hours_line = hours if hours else "Hours not confirmed"
        points.append(
            {
                "coordinates": [_field(place, "lon"), _field(place, "lat")],
                "tip": f"{str(role).title()} · {_field(place, 'name')} · {hours_line}",
                "color": color,
            }
        )
    sponsored = _field(state.get("report"), "sponsored")
    if sponsored is not None:
        points.append(
            {
                "coordinates": [_field(sponsored, "lon"), _field(sponsored, "lat")],
                "tip": f"Sponsored · {_field(sponsored, 'name')}",
                "color": _SPONSOR,
            }
        )
    layers = []
    if coords:
        layers.append(
            pdk.Layer(
                "PathLayer",
                [{"path": coords, "color": _ROUTE_CASING}],
                get_path="path",
                get_color="color",
                get_width=12,
                width_min_pixels=8,
                pickable=False,
            )
        )
        layers.append(
            pdk.Layer(
                "PathLayer",
                [{"path": coords, "color": _ROUTE}],
                get_path="path",
                get_color="color",
                get_width=5,
                width_min_pixels=3,
                pickable=False,
            )
        )
    if points:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                points,
                get_position="coordinates",
                get_fill_color="color",
                get_line_color=[255, 255, 255],
                stroked=True,
                line_width_min_pixels=2,
                get_radius=90,
                radius_min_pixels=8,
                pickable=True,
            )
        )
    if sponsored is not None:
        layers.append(_sponsor_label_layer([sponsored]))
    if not layers:
        return
    focus = coords or [point["coordinates"] for point in points]
    catalog = _shown_catalog(sponsored)
    layers.extend(_catalog_layers(catalog))
    deck = pdk.Deck(
        layers=layers,
        initial_view_state=_view(focus),
        map_provider="carto",
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        tooltip={"text": "{tip}"},
    )
    st.pydeck_chart(deck, height=640)
    _map_footer(sponsored is not None or bool(catalog))


def _shown_catalog(sponsored: Any = None) -> list[dict[str, Any]]:
    """Catalog sponsors to draw when the toggle is on, minus the one the route already shows."""
    if not st.session_state.get("show_sponsors"):
        return []
    taken = None
    if sponsored is not None:
        taken = (_field(sponsored, "lon"), _field(sponsored, "lat"))
    return [record for record in load_sponsors() if (record["lon"], record["lat"]) != taken]


def _map_footer(has_sponsor: bool) -> None:
    with st.container(key="mapfoot"):
        legend, toggle = st.columns([1, 0.5], vertical_alignment="center", gap=12)
        with legend:
            st.markdown(_legend_html(has_sponsor), unsafe_allow_html=True)
        with toggle:
            _sponsor_toggle()


def _metrics(state: dict[str, Any]) -> None:
    candidates = list(state.get("candidates") or [])
    report = state.get("report")
    chosen_id = _field(report, "chosen_route")
    candidate = next((item for item in candidates if _field(item, "id") == chosen_id), None)
    if candidate is None and candidates:
        candidate = candidates[0]
    if candidate is None:
        return
    distance = _field(candidate, "distance_m")
    climb = _field(candidate, "ascend_m")
    share = _field(candidate, "bike_network_share")
    minutes = _minutes(candidate)
    cards = [
        ("Mode", _mode_name(_field(candidate, "mode"))),
        ("Distance", f"{round(distance / 1000, 1)} km" if distance is not None else "—"),
        ("Time", f"{minutes} min" if minutes is not None else "—"),
        ("Climb", f"{int(round(climb))} m" if climb is not None else "—"),
    ]
    if share is not None:
        cards.append(("Bike lanes", f"{round(share * 100)}%"))
    cells = "".join(
        f"<div class='rc-metric'><span>{escape(label)}</span><strong>{escape(value)}</strong></div>"
        for label, value in cards
    )
    st.markdown(
        f"<div class='rc-metrics' style='grid-template-columns:repeat({len(cards)},minmax(0,1fr))'>{cells}</div>",
        unsafe_allow_html=True,
    )


def _places(state: dict[str, Any]) -> None:
    places = list(state.get("places") or [])
    if not places:
        return
    rows = []
    for place in places:
        hours = _field(place, "opening_hours")
        hours_line = hours if hours else "Hours not confirmed"
        detour = _field(place, "detour_km")
        extra = f" · detour {detour} km" if detour is not None else ""
        role = str(_field(place, "role", "") or "place")
        rows.append(
            "<div class='rc-row'>"
            f"<span class='rc-pip {escape(role)}'></span>"
            "<div>"
            f"<div class='rc-kicker'>{escape(role.replace('_', ' '))}</div>"
            f"<b>{escape(str(_field(place, 'name') or ''))}</b>"
            f"<p>{escape(str(hours_line))}{escape(extra)}</p>"
            "</div><span></span></div>"
        )
    st.markdown(
        "<section class='rc-panel'><div class='rc-section'>Places</div>"
        + "".join(rows)
        + "</section>",
        unsafe_allow_html=True,
    )


def _checks(state: dict[str, Any]) -> None:
    report = state.get("report")
    checks = list(_field(report, "checks") or [])
    sponsored = _field(report, "sponsored")
    if not checks and sponsored is None:
        return
    rows = []
    for check in checks:
        status = str(_field(check, "status") or "")
        rows.append(
            "<div class='rc-row'>"
            f"<span class='rc-pip {escape(status)}'></span>"
            "<div>"
            f"<b>{escape(_pretty_id(str(_field(check, 'id') or '')))}</b>"
            f"<p>{escape(str(_field(check, 'evidence') or ''))}</p>"
            "</div>"
            f"<span class='rc-badge {escape(status)}'>{escape(status)}</span>"
            "</div>"
        )
    if sponsored is not None:
        distance = _field(sponsored, "distance_m")
        away = f"{int(round(distance))} m from the route" if distance is not None else ""
        detail = " · ".join(
            part
            for part in (
                str(_field(sponsored, "category") or "Place"),
                f"rating {_field(sponsored, 'rating')}",
                away,
            )
            if part
        )
        rows.append(
            "<div class='rc-row'>"
            "<span class='rc-pip sponsored'></span>"
            "<div><div class='rc-kicker'>Sponsored</div>"
            f"<b>{escape(str(_field(sponsored, 'name') or ''))}</b>"
            f"<p>{escape(detail)}</p></div><span></span></div>"
        )
    st.markdown(
        "<section class='rc-panel'><div class='rc-section'>What I checked</div>"
        + "".join(rows)
        + "</section>",
        unsafe_allow_html=True,
    )


def _support() -> None:
    tickets = read_handoffs()
    if not tickets:
        st.markdown(
            "<section class='rc-support'><div class='rc-section'>Support</div>"
            "<strong>Nothing is waiting</strong>"
            "<p>When a trip needs a person, it shows up here with the reason and the masked request.</p>"
            "</section>",
            unsafe_allow_html=True,
        )
        return
    blocks = []
    for ticket in reversed(tickets):
        reason = ticket.get("reason") or ticket.get("handoff_reason") or "handoff"
        text = ticket.get("redacted_request") or ticket.get("text") or ""
        blocks.append(
            "<div class='rc-ticket'>"
            f"<div class='rc-kicker'>{escape(str(ticket.get('request_id', '')))}</div>"
            f"<b>{escape(str(reason))}</b>"
            f"<p>{escape(str(text))}</p>"
            "</div>"
        )
    st.markdown(
        "<section class='rc-support'><div class='rc-section'>Support</div>" + "".join(blocks) + "</section>",
        unsafe_allow_html=True,
    )


def _base_map() -> None:
    """Downtown Toronto, shown before a route exists so the map already fills the screen."""
    catalog = _shown_catalog()
    deck = pdk.Deck(
        layers=_catalog_layers(catalog),
        initial_view_state=pdk.ViewState(latitude=43.6532, longitude=-79.3832, zoom=12, pitch=0),
        map_provider="carto",
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        tooltip={"text": "{tip}"},
    )
    st.pydeck_chart(deck, height=720)
    _map_footer(bool(catalog))


def _facts(state: dict[str, Any]) -> None:
    places = list(state.get("places") or [])
    checks = list(_field(state.get("report"), "checks") or [])
    sponsored = _field(state.get("report"), "sponsored")
    if not places and not checks and sponsored is None:
        return
    with st.container(key="facts"):
        if places and (checks or sponsored is not None):
            place_col, check_col = st.columns(2, gap=12)
            with place_col:
                _places(state)
            with check_col:
                _checks(state)
        elif places:
            _places(state)
        else:
            _checks(state)


def _stage(state: dict[str, Any] | None) -> None:
    with st.container(key="stage"):
        if state is None:
            _base_map()
            st.markdown(
                "<p class='rc-caption'>Downtown Toronto. A route draws here after you plan a trip.</p>",
                unsafe_allow_html=True,
            )
            return
        _metrics(state)
        _map(state)
        _facts(state)


def _topbar(sample: bool) -> None:
    pill = "<span class='rc-pill'>Sample trip</span>" if sample else ""
    with st.container(key="topbar"):
        title, action = st.columns([1, 0.18], vertical_alignment="center", gap=12)
        with title:
            st.markdown(
                "<div class='rc-brand'>"
                "<span class='rc-mark' aria-hidden='true'>"
                "<svg width='18' height='18' viewBox='0 0 18 18' fill='none'>"
                "<circle cx='4.2' cy='13.2' r='1.7' fill='white'/>"
                "<circle cx='13.6' cy='4.8' r='1.7' fill='white'/>"
                "<path d='M5.6 11.8 L12.2 6.2' stroke='white' stroke-width='1.6' stroke-linecap='round'/>"
                "</svg></span>"
                "<div class='rc-brand-copy'><h1>RouteChat</h1>"
                "<p>Conversational trip planner</p></div>"
                f"{pill}</div>",
                unsafe_allow_html=True,
            )
        with action:
            if st.button("New chat", icon=":material/maps_ugc:", key="new_chat", width="stretch"):
                _new_chat()
                st.rerun()


def _conversation(sample: bool) -> str | None:
    with st.container(key="rail"):
        st.markdown("<div class='rc-panel-title'>Conversation</div>", unsafe_allow_html=True)
        with st.container(key="thread"):
            if not st.session_state.history:
                hint = (
                    "<p class='rc-hint'>Sample mode answers with the saved King Station ride.</p>"
                    if sample
                    else ""
                )
                st.markdown(
                    "<div class='rc-empty'><p class='rc-kicker'>New trip</p><h2>Plan a trip</h2>"
                    "<p>Another place stays in this chat. New chat clears the thread and the map.</p>"
                    f"{hint}</div>",
                    unsafe_allow_html=True,
                )
                if st.button(
                    "Try the sample ride",
                    type="primary",
                    icon=":material/directions_bike:",
                    key="sample_ride",
                    width="stretch",
                ):
                    _ask(RUNNING_UTTERANCE)
                    st.rerun()
            else:
                for message in st.session_state.history:
                    with st.chat_message(message["role"]):
                        st.markdown(message["content"])
        _support()
        st.markdown("<div class='rc-field-label'>Trip request</div>", unsafe_allow_html=True)
        return st.chat_input(
            "Where are you heading?",
            key=f"composer-{st.session_state.composer_epoch}",
        )


def main() -> None:
    st.set_page_config(page_title="RouteChat", page_icon="🗺️", layout="wide")
    st.markdown(_CSS, unsafe_allow_html=True)
    _init()
    sample = os.environ.get("STUBS") == "1"
    _topbar(sample)
    rail, stage = st.columns([0.28, 0.72], gap=16, vertical_alignment="top")
    prompt = None
    with rail:
        prompt = _conversation(sample)
    if prompt:
        _ask(prompt)
        st.rerun()
    with stage:
        _stage(st.session_state.last)


if __name__ == "__main__":
    main()
