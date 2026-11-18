# Owner: Person A (WP7 app), Team Rosedale, MMAI 894.
# Only Person A edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP7.
# Written for this project with AI assistance; no code copied from other sources.
"""WP7 Streamlit app. Chat on the left, the trip on the right.

The visual language follows EyesRhythm: a light paper background, Inter-like
type, soft white cards, and one blue. The map is a street basemap with the
route line and the place pins drawn on top. STUBS=1 is the default until the
other stage packages are merged, so this screen can run on its own.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

os.environ.setdefault("STUBS", "1")

import pydeck as pdk
import streamlit as st

from routechat.graph import invoke
from routechat.logging import append_handoff, log_event, mask_for_log, read_handoffs
from routechat.stubs import RUNNING_UTTERANCE

_ROUTE = [47, 120, 196]
_START = [47, 120, 196, 230]
_STOP = [224, 161, 90, 235]
_END = [224, 122, 114, 230]
_SPONSOR = [91, 159, 224, 220]

_CSS = """
<style>
:root {
  --bg: #f2f5f9;
  --card: rgba(255, 255, 255, 0.92);
  --stroke: rgba(30, 48, 72, 0.09);
  --text: #1e2c3a;
  --soft: #5a6f82;
  --faint: #8a9aab;
  --blue: #5b9fe0;
  --blue-deep: #2f78c4;
  --peach: #e8b896;
  --amber: #e0a15a;
  --rose: #e07a72;
  --shadow: 0 10px 30px rgba(30, 48, 72, 0.07);
}
html, body, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
  background: var(--bg);
  color: var(--text);
  font-family: Inter, "SF Pro Text", "Segoe UI", system-ui, sans-serif;
}
[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 1.2rem; max-width: 1180px; }
h1, h2, h3, p, label, span { font-family: Inter, "SF Pro Text", "Segoe UI", system-ui, sans-serif; }
.rc-brand { display: flex; align-items: baseline; gap: 12px; margin-bottom: 4px; }
.rc-brand h1 { font-size: 28px; font-weight: 650; letter-spacing: -0.03em; margin: 0; color: var(--text); }
.rc-pill {
  font-size: 12px; color: var(--blue-deep); background: #e7f1fb;
  border-radius: 999px; padding: 4px 10px; font-weight: 600;
}
.rc-lead { color: var(--soft); margin: 0 0 16px 0; font-size: 15px; }
.rc-hero, .rc-card, .rc-metric, .rc-place, .rc-check {
  background: var(--card);
  border: 1px solid var(--stroke);
  border-radius: 16px;
  box-shadow: var(--shadow);
}
.rc-hero { padding: 28px 26px 22px; }
.rc-hero h2 { margin: 0 0 6px 0; font-size: 26px; letter-spacing: -0.03em; }
.rc-hero p { color: var(--soft); margin: 0; }
.rc-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; margin: 0 0 12px 0; }
.rc-metric { padding: 12px 14px; }
.rc-metric span { display: block; color: var(--faint); font-size: 12px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; }
.rc-metric strong { display: block; margin-top: 2px; font-size: 22px; letter-spacing: -0.03em; color: var(--text); }
.rc-section { margin: 16px 0 8px; font-size: 13px; font-weight: 650; color: var(--soft); letter-spacing: 0.04em; text-transform: uppercase; }
.rc-places, .rc-checks { display: grid; gap: 10px; }
.rc-place, .rc-check { padding: 12px 14px; }
.rc-place b, .rc-check b { font-size: 15px; }
.rc-place p, .rc-check p { margin: 4px 0 0; color: var(--soft); font-size: 13px; }
.rc-kicker { color: var(--blue-deep); font-size: 11px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; }
.rc-dot { display: inline-block; width: 8px; height: 8px; border-radius: 99px; margin-right: 6px; }
.met { background: var(--blue-deep); }
.unmet { background: var(--rose); }
.unverifiable { background: var(--amber); }
.rc-legend { color: var(--soft); font-size: 13px; margin: 6px 0 0; }
.rc-legend i { display: inline-block; width: 8px; height: 8px; border-radius: 99px; margin: 0 6px 0 12px; }
[data-testid="stChatMessage"] {
  background: white;
  border: 1px solid var(--stroke);
  border-radius: 16px;
}
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
    zoom = 14 if span < 0.03 else 13 if span < 0.08 else 11
    return pdk.ViewState(
        longitude=sum(lons) / len(lons),
        latitude=sum(lats) / len(lats),
        zoom=zoom,
        pitch=0,
        bearing=0,
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
                "tip": f"{role.title()} · {_field(place, 'name')} · {hours_line}",
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
                [{"path": coords, "color": _ROUTE}],
                get_path="path",
                get_color="color",
                get_width=7,
                width_min_pixels=4,
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
                get_radius=80,
                radius_min_pixels=7,
                pickable=True,
            )
        )
    if not layers:
        return
    focus = coords or [point["coordinates"] for point in points]
    deck = pdk.Deck(
        layers=layers,
        initial_view_state=_view(focus),
        map_provider="carto",
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        tooltip={"text": "{tip}"},
    )
    st.pydeck_chart(deck, height=480)
    st.markdown(
        "<p class='rc-legend'><i style='background:#2f78c4'></i>Start"
        "<i style='background:#e0a15a'></i>Stop"
        "<i style='background:#e07a72'></i>End</p>",
        unsafe_allow_html=True,
    )


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
    cards = [
        ("Mode", _mode_name(_field(candidate, "mode"))),
        ("Distance", f"{round(distance / 1000, 1)} km" if distance is not None else "—"),
        ("Time", f"{_minutes(candidate)} min" if _minutes(candidate) is not None else "—"),
        ("Climb", f"{int(round(climb))} m" if climb is not None else "—"),
    ]
    if share is not None:
        cards.append(("Bike lanes", f"{round(share * 100)}%"))
    html = "<div class='rc-metrics'>" + "".join(
        f"<div class='rc-metric'><span>{label}</span><strong>{value}</strong></div>" for label, value in cards
    )
    st.markdown(html + "</div>", unsafe_allow_html=True)


def _places(state: dict[str, Any]) -> None:
    places = list(state.get("places") or [])
    if not places:
        return
    st.markdown("<div class='rc-section'>Places</div>", unsafe_allow_html=True)
    blocks = []
    for place in places:
        hours = _field(place, "opening_hours")
        hours_line = hours if hours else "Hours not confirmed"
        detour = _field(place, "detour_km")
        extra = f" · detour {detour} km" if detour is not None else ""
        role = str(_field(place, "role", "")).replace("_", " ").title()
        blocks.append(
            "<div class='rc-place'>"
            f"<div class='rc-kicker'>{role}</div>"
            f"<b>{_field(place, 'name')}</b>"
            f"<p>{hours_line}{extra}</p>"
            "</div>"
        )
    st.markdown("<div class='rc-places'>" + "".join(blocks) + "</div>", unsafe_allow_html=True)


def _checks(state: dict[str, Any]) -> None:
    report = state.get("report")
    checks = list(_field(report, "checks") or [])
    if not checks:
        return
    st.markdown("<div class='rc-section'>What I checked</div>", unsafe_allow_html=True)
    blocks = []
    for check in checks:
        status = str(_field(check, "status"))
        label = str(_field(check, "id")).replace("_", " ").replace(":", " · ")
        blocks.append(
            "<div class='rc-check'>"
            f"<div class='rc-kicker'><span class='rc-dot {status}'></span>{status}</div>"
            f"<b>{label}</b>"
            f"<p>{_field(check, 'evidence')}</p>"
            "</div>"
        )
    st.markdown("<div class='rc-checks'>" + "".join(blocks) + "</div>", unsafe_allow_html=True)
    sponsored = _field(report, "sponsored")
    if sponsored is not None:
        distance = _field(sponsored, "distance_m")
        away = f"{int(round(distance))} m from the route" if distance is not None else ""
        st.markdown(
            "<div class='rc-place' style='margin-top:10px'>"
            "<div class='rc-kicker'>Sponsored</div>"
            f"<b>{_field(sponsored, 'name')}</b>"
            f"<p>{_field(sponsored, 'category') or 'Place'} · rating {_field(sponsored, 'rating')} · {away}</p>"
            "</div>",
            unsafe_allow_html=True,
        )


def _support() -> None:
    tickets = read_handoffs()
    if not tickets:
        st.markdown(
            "<div class='rc-hero'><h2>Nothing is waiting</h2>"
            "<p>When a trip needs a person, it shows up here with the reason and the masked request.</p></div>",
            unsafe_allow_html=True,
        )
        return
    for ticket in reversed(tickets):
        reason = ticket.get("reason") or ticket.get("handoff_reason") or "handoff"
        text = ticket.get("redacted_request") or ticket.get("text") or ""
        st.markdown(
            "<div class='rc-place'>"
            f"<div class='rc-kicker'>{ticket.get('request_id', '')}</div>"
            f"<b>{reason}</b>"
            f"<p>{text}</p>"
            "</div>",
            unsafe_allow_html=True,
        )


def _trip(state: dict[str, Any] | None) -> None:
    if state is None:
        st.markdown(
            "<div class='rc-hero'><h2>Where should we go?</h2>"
            "<p>Ask for a walk, a run, a ride, or a drive. The route is drawn on the map, "
            "and every warning the checker could not clear stays on the screen.</p></div>",
            unsafe_allow_html=True,
        )
        return
    _metrics(state)
    _map(state)
    _places(state)
    _checks(state)


def main() -> None:
    st.set_page_config(page_title="RouteChat", page_icon="🗺️", layout="wide")
    st.markdown(_CSS, unsafe_allow_html=True)
    _init()
    sample = os.environ.get("STUBS") == "1"
    pill = "<span class='rc-pill'>Sample trip</span>" if sample else ""
    st.markdown(
        f"<div class='rc-brand'><h1>RouteChat</h1>{pill}</div>"
        "<p class='rc-lead'>A quiet planner for one personal trip. Walking, running, cycling, and driving.</p>",
        unsafe_allow_html=True,
    )
    chat, board = st.columns([0.40, 0.60], gap="large")
    with chat:
        if not st.session_state.history:
            st.markdown(
                "<div class='rc-hero'><h2>Plan a trip</h2>"
                "<p>Try the King Station example, or describe your own.</p></div>",
                unsafe_allow_html=True,
            )
            if st.button("Try King Station to the University of Toronto", type="primary"):
                _ask(RUNNING_UTTERANCE)
                st.rerun()
        for message in st.session_state.history:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
    prompt = st.chat_input("Where are you heading?")
    if prompt:
        _ask(prompt)
        st.rerun()
    with board:
        trip_tab, support_tab = st.tabs(["Trip", "Support"])
        with trip_tab:
            _trip(st.session_state.last)
        with support_tab:
            _support()


if __name__ == "__main__":
    main()
