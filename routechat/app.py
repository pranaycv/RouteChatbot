# Owner: Person A (WP7 app), Team Rosedale, MMAI 894.
# Only Person A edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP7.
# Written for this project with AI assistance; no code copied from other sources.
"""WP7 Streamlit app. Conversation on the left, the trip on the right.

The visual language follows EyesRhythm: a light paper background, Inter-like
type, soft white cards, and one blue. The map is a light, street, dark, or
satellite basemap with the route line and the place pins drawn on top. Places and checks sit under the
map. Keys come from .env. With no model key, and no recorded replies to
replay, the screen shows the saved sample trip.
"""

from __future__ import annotations

import base64
import logging
import math
import os
import re
import time
from contextlib import nullcontext
from html import escape
from pathlib import Path
from typing import Any
from uuid import uuid4

import orjson
import pydeck as pdk
import streamlit as st
from dotenv import load_dotenv

from routechat.agents.rewriter import clean_message
from routechat.classifier.predict import adapter_dir
from routechat.config import get_settings
from routechat.evidence import tidy_evidence
from routechat.device import device_place
from routechat.graph import invoke, is_new_trip, question_key, resume, wants_device
from routechat.logging import append_handoff, mask_for_log, read_handoffs
from routechat.progress import board_html, doing, done, summary_html
from routechat.stubs import RUNNING_UTTERANCE
from routechat import keys, locate

_ROOT = Path(__file__).resolve().parents[1]


def _configure() -> None:
    """Read .env, then fill the settings it leaves blank. A value already set always wins.

    ROUTECHAT_DOTENV names another file; an empty value reads none, as the tests do.
    """
    dotenv = os.environ.get("ROUTECHAT_DOTENV", str(_ROOT / ".env"))
    if dotenv:
        load_dotenv(dotenv)
    if not os.environ.get("STUBS"):
        has_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
        replay = os.environ.get("FIXTURES", "").strip().lower() == "replay"
        os.environ["STUBS"] = "0" if has_key or replay or keys.from_visitors() else "1"
    # At INFO, httpx logs whole addresses, and a map address holds the key.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # The fine-tuned classifier is the default backend. Until its adapter is on disk, prompt instead.
    if not os.environ.get("CLASSIFIER_BACKEND") and not adapter_dir().exists():
        os.environ["CLASSIFIER_BACKEND"] = "prompted_small"


def _own_keys() -> bool:
    """True when this server asks each visitor for their keys."""
    return keys.from_visitors() and os.environ.get("STUBS") != "1"


def _sample() -> bool:
    """True when this visitor gets the saved sample trip instead of the real stages."""
    return os.environ.get("STUBS") == "1" or (_own_keys() and not st.session_state.get("own_keys"))


_ROUTE = [47, 120, 196]
_ROUTE_CASING = [255, 255, 255]
_START = [47, 120, 196, 230]
_STOP = [224, 161, 90, 235]
_END = [224, 122, 114, 230]
_SPONSOR = [91, 159, 224, 220]
_SPONSOR_INK = [31, 94, 158, 255]
SPONSOR_LABEL = "Sponsored"

_ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services"
_ESRI_CREDIT = "Imagery: Esri, Vantor, Earthstar Geographics, and the GIS User Community"
_SATELLITE = {
    "version": 8,
    "sources": {
        # Past zoom 18 Esri has no photo of many places outside the city, so the map stretches this one.
        "imagery": {
            "type": "raster",
            "tiles": [f"{_ESRI}/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}"],
            "tileSize": 256,
            "maxzoom": 18,
            "attribution": _ESRI_CREDIT,
        },
        "names": {
            "type": "raster",
            "tiles": [f"{_ESRI}/Reference/World_Boundaries_and_Places/MapServer/tile/{{z}}/{{y}}/{{x}}"],
            "tileSize": 256,
            "maxzoom": 18,
        },
    },
    "layers": [
        {"id": "imagery", "type": "raster", "source": "imagery"},
        {"id": "names", "type": "raster", "source": "names"},
    ],
}
_DEFAULT_VIEW = "Street"
# pydeck takes a style object only for Mapbox, so the satellite style travels as a data URL.
_MAP_VIEWS = {
    "Light": "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
    "Street": "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
    "Dark": "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
    "Satellite": "data:application/json;base64," + base64.b64encode(orjson.dumps(_SATELLITE)).decode(),
}

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
  padding: 16px !important;
  max-width: 100% !important;
}
/* The block that carries this style sheet is empty, but Streamlit still puts a gap after it. */
[data-testid="stElementContainer"]:has(style) { display: none !important; }
button, input, textarea, [data-testid="stMarkdown"], [data-testid="stMarkdownContainer"] p {
  font-family: var(--font);
}

.st-key-topbar {
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid var(--stroke);
  border-radius: 16px;
  box-shadow: var(--shadow-soft);
  padding: 12px 16px !important;
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

@media (min-width: 901px) {
  [data-testid="stColumn"]:has(.st-key-rail) {
    flex: 0 0 var(--rail) !important;
    width: var(--rail) !important;
    min-width: var(--rail) !important;
    max-width: var(--rail) !important;
  }
  [data-testid="stColumn"]:has(.st-key-stage) {
    /* A basis of 0, so a wide row under the map never pushes the stage below the rail. */
    flex: 1 1 0 !important;
    width: auto !important;
    min-width: 0 !important;
  }
}

.st-key-rail, .st-key-stage {
  background: var(--card);
  border: 1px solid var(--stroke);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  /* Streamlit's column flex-basis ignores a plain height, so pin all three.
     118px is the 70px top bar and the 16px gaps above it, below it, and under the panels. */
  flex: 0 0 auto !important;
  height: calc(100dvh - 118px) !important;
  min-height: calc(100dvh - 118px) !important;
  max-height: calc(100dvh - 118px) !important;
  overflow: hidden !important;
  display: flex !important;
  flex-direction: column !important;
  gap: 8px !important;
}
.st-key-rail { padding: 16px !important; }
/* The stage scrolls when a short screen cannot hold the map and the checks. */
.st-key-stage { padding: 16px !important; overflow: hidden auto !important; }
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
/* Reversed, so the area rests at its end: the newest message and step are in view without scrolling. */
.st-key-thread { flex-direction: column-reverse !important; justify-content: flex-start !important; }
.st-key-thread > * { flex: 0 0 auto !important; margin-bottom: auto !important; }
.st-key-messages { gap: 4px !important; }
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
.rc-input {
  margin: 8px 0 0 !important; padding: 8px 0 0 !important; border-top: 1px solid rgba(47, 120, 196, 0.16);
  color: var(--soft); font-size: 12.5px !important; line-height: 1.4 !important;
}
.rc-input b { display: block; color: var(--blue-ink); font-size: 10.5px; letter-spacing: 0.05em; text-transform: uppercase; }
.rc-steps { list-style: none; margin: 0 !important; padding: 0 !important; }
.rc-steps li {
  position: relative; margin: 0 !important; padding: 3px 0 3px 22px !important;
  color: var(--soft); font-size: 13px !important; line-height: 1.4 !important;
}
.rc-steps li.done::before { content: "✓"; position: absolute; left: 2px; color: var(--blue-deep); font-weight: 700; }
.rc-steps li.now { color: var(--text); font-weight: 600; }
.rc-steps li.now::before {
  content: ""; position: absolute; left: 1px; top: 7px; width: 10px; height: 10px; border-radius: 99px;
  border: 2px solid rgba(47, 120, 196, 0.25); border-top-color: var(--blue-deep); animation: rc-spin 0.8s linear infinite;
}
@keyframes rc-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .rc-steps li.now::before { animation: none; } }
.rc-done { margin-top: 8px; padding-top: 8px; border-top: 1px solid rgba(30, 48, 72, 0.07); }
.rc-done summary { color: var(--faint); font-size: 12px; cursor: pointer; }
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
  height: calc(100dvh - 234px) !important;
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

/* Tablets on their side: a narrower rail, and a map that keeps a useful height. */
@media (min-width: 901px) and (max-width: 1099px) {
  :root { --rail: 310px; }
  .st-key-stage > div:has([data-testid="stDeckGlJsonChart"]) { min-height: 260px !important; }
}

/* Phones, upright tablets, and phones on their side: one column that scrolls as a page. */
@media (max-width: 900px), (max-height: 560px) {
  [data-testid="stMain"] { overflow: hidden auto !important; }
  .block-container { padding: 12px !important; }
  .block-container > [data-testid="stVerticalBlock"] { gap: 12px !important; }

  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-rail) {
    flex-direction: column !important;
    gap: 12px !important;
  }
  [data-testid="stColumn"]:has(.st-key-rail),
  [data-testid="stColumn"]:has(.st-key-stage) {
    flex: 0 0 auto !important;
    width: 100% !important;
    min-width: 0 !important;
    max-width: 100% !important;
  }
  .st-key-rail, .st-key-stage {
    height: auto !important;
    min-height: 0 !important;
    max-height: none !important;
    overflow: visible !important;
    padding: 12px !important;
  }
  .st-key-rail > div:has(.st-key-thread), .st-key-thread {
    flex: 0 0 auto !important;
    overflow: visible !important;
  }
  /* The box to type in comes straight after the reply. Support follows it. */
  .st-key-rail > div:has([data-testid="stChatInput"]) { order: 1; }
  .st-key-rail > div:has(.rc-support) { order: 2; margin-top: 4px !important; }

  /* Short enough that a thumb can still scroll the page beside the map. */
  .st-key-stage > div:has([data-testid="stDeckGlJsonChart"]) {
    flex: 0 0 auto !important;
    height: clamp(220px, 48svh, 460px) !important;
  }
  .st-key-stage [data-testid="stDeckGlJsonChart"],
  .st-key-stage:not(:has(.st-key-facts)) [data-testid="stDeckGlJsonChart"] {
    height: 100% !important;
    min-height: 0 !important;
  }

  .st-key-topbar { padding: 10px 12px !important; }
  .st-key-topbar [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; }
  .st-key-topbar [data-testid="stColumn"] { min-width: 0 !important; flex: 1 1 0 !important; }
  .st-key-topbar [data-testid="stColumn"]:last-child {
    flex: 0 0 128px !important;
    width: 128px !important;
    min-width: 128px !important;
  }
  .st-key-topbar [data-testid="stBaseButton-secondary"] {
    min-height: 44px !important;
    height: 44px !important;
  }
  /* Under 16px, an iPhone zooms the page in when the box is tapped. */
  [data-testid="stChatInputTextArea"] { font-size: 16px !important; }
  [data-testid="stChatInputSubmitButton"] { min-width: 40px !important; min-height: 40px !important; }
  .st-key-map_view button { min-height: 44px !important; }
  .st-key-show_sponsors label { min-height: 44px; align-items: center; }
}

@media (max-width: 600px) {
  .rc-brand { display: grid; grid-template-columns: 36px minmax(0, 1fr); gap: 2px 10px; align-content: center; }
  .rc-mark { grid-row: span 2; align-self: center; }
  .rc-brand-copy { align-self: center; }
  .rc-brand:not(:has(.rc-pill)) .rc-brand-copy { grid-row: span 2; }
  .rc-brand-copy h1 { white-space: nowrap; }
  .rc-brand-copy p { display: none; }
  .rc-pill { justify-self: start; }

  /* Four numbers as two by two, five as three and two. The lines are the gaps. */
  .rc-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
    gap: 1px;
    background: #e6ebf1;
  }
  .rc-metrics:has(.rc-metric:nth-child(5)) { grid-template-columns: repeat(6, minmax(0, 1fr)) !important; }
  .rc-metrics:has(.rc-metric:nth-child(5)) .rc-metric { grid-column: span 2; }
  .rc-metrics:has(.rc-metric:nth-child(5)) .rc-metric:nth-child(n + 4) { grid-column: span 3; }
  .rc-metric { border-right: none; background: var(--inset); padding: 10px 12px; min-height: 56px; }

  .st-key-mapfoot { gap: 6px !important; }
  .st-key-map_view { flex: 1 0 100% !important; }
  .st-key-map_view, .st-key-map_view [data-testid="stButtonGroup"],
  .st-key-map_view [role="radiogroup"] { width: 100% !important; }
  .st-key-map_view button { flex: 1 1 0 !important; min-width: 0 !important; padding: 4px 6px !important; }
  .rc-legend { gap: 12px; flex-wrap: wrap; }
  .rc-panel { padding: 12px; }
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
    st.session_state.setdefault("turns", [])
    st.session_state.setdefault("known", None)
    st.session_state.setdefault("clarify_count", 0)
    st.session_state.setdefault("pending", None)
    st.session_state.setdefault("asked", [])
    st.session_state.setdefault("options", [])
    st.session_state.setdefault("trips", [])
    st.session_state.setdefault("full", None)
    st.session_state.setdefault("picked", [])
    st.session_state.setdefault("last", None)
    st.session_state.setdefault("waiting", None)
    st.session_state.setdefault("queue", [])
    st.session_state.setdefault("seen", [])
    st.session_state.setdefault("no_location", False)
    st.session_state.setdefault("composer_epoch", 0)
    st.session_state.setdefault("tickets", [])
    st.session_state.setdefault("own_keys", {})
    st.session_state.setdefault("keys_open", False)


def _new_chat() -> None:
    """Drop this thread. The next message starts from a blank map."""
    st.session_state.history = []
    st.session_state.turns = []
    st.session_state.known = None
    st.session_state.clarify_count = 0
    st.session_state.pending = None
    st.session_state.asked = []
    st.session_state.options = []
    st.session_state.trips = []
    st.session_state.full = None
    st.session_state.picked = []
    st.session_state.last = None
    st.session_state.waiting = None
    st.session_state.queue = []
    st.session_state.seen = []
    st.session_state.no_location = False
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


def _with_pending(utterance: str) -> str:
    """The message a follow-up question is waiting on, then its answer.

    A clarify stops before extraction, so "From Union Station" alone has no trip
    to attach to. The guard reads the joined text, and an answer that would pass
    the length limit goes alone.
    """
    pending = st.session_state.pending
    if not pending:
        return utterance
    joined = f"{pending.rstrip(' .')}. {utterance}"
    return joined if len(joined) <= get_settings().guard.max_chars else utterance


_FILLER = frozenset("a an and at by de du go i in is it la le me of on one or please that the this to".split())
_SEEN = 20  # places of earlier trips that a follow-up may mean
_PICK_WORDS = 8  # a longer message says more than which place, so the rewriter reads it


def _ordinal(words: set[str], count: int) -> int | None:
    """Which of count offers a short answer points at, by its number, or by yes when there is one."""
    if len(words) > 4:
        return None
    if count == 1 and words & {"yes", "yeah", "yep", "ok", "okay", "sure", "correct", "right", "1"}:
        return 0
    numbers = ({"1", "first"}, {"2", "second"}, {"3", "third"})[:count] if count > 1 else ()
    named = [index for index, names in enumerate(numbers) if words & names]
    return named[0] if len(named) == 1 else None


def _pick_trip(utterance: str) -> str | None:
    """The trip this answer chooses when the app asked which one, as a full request."""
    trips, st.session_state.trips = st.session_state.trips, []
    words = set(re.findall(r"\w+", utterance.casefold())) - _FILLER
    # Only a bare choice. "the first one but by bike" is for the rewriter.
    if not words or words - {"first", "second", "third", "1", "2", "3", "trip", "yes", "yeah", "yep", "ok", "okay", "sure"}:
        return None
    number = _ordinal(words, len(trips))
    return None if number is None else trips[number]


def _pick(utterance: str) -> Any:
    """The offered place this answer names, or None."""
    options = st.session_state.options
    said = re.findall(r"\w+", utterance.casefold())
    words = set(said) - _FILLER
    if not options or not words or len(said) > _PICK_WORDS:
        return None
    number = _ordinal(words, len(options))
    if number is not None:
        return options[number]
    if len(options) == 1:
        return None
    # Words every option shares, such as the place's own name, do not tell them apart.
    labels = [set(re.findall(r"\w+", str(_field(option, "name")).casefold())) for option in options]
    shared = set.intersection(*labels)
    # Every word must be about a place on offer: "bike to Alberta instead" is a new request.
    if not words <= set.union(*labels):
        return None
    named = [option for option, label in zip(options, labels) if words & (label - shared)]
    return named[0] if len(named) == 1 else None


def _whole_request(place: Any) -> str | None:
    """The last complete request with the chosen place written in, or None when it cannot be done safely."""
    asked = str(_field(place, "query") or "")
    if not st.session_state.full or not asked:
        return None
    found = re.findall(rf"(?<!\w){re.escape(asked)}(?!\w)", st.session_state.full, re.IGNORECASE)
    # The name of both ends, or a name reworded since, does not say which words to replace.
    if len(found) != 1:
        return None
    return st.session_state.full.replace(found[0], str(_field(place, "name")))


def _answer(utterance: str) -> tuple[str, bool]:
    """Turn a choice between offered places into a request. True when it is a complete one.

    A complete request needs nothing from before. Otherwise it is a change, and the
    extractor adds it to the trip already known.
    """
    place = _pick(utterance)
    st.session_state.options = []
    if place is None:
        return utterance, False
    st.session_state.picked = [*st.session_state.picked, place]
    whole = _whole_request(place)
    if whole:
        return whole, True
    end = "start" if _field(place, "role") == "origin" else "destination"
    return f"Change the {end} to {_field(place, 'name')}", False


def _planner_input(state: dict[str, Any], utterance: str) -> tuple[str, str] | None:
    """What the classifier read for this turn, as a label and a text. None when the guard stopped it."""
    guard = state.get("guard")
    if _field(guard, "decision") != "allow" or _sample():
        return None
    rewritten = state.get("rewrite")
    status = _field(rewritten, "status")
    if status == "ready":
        return "Refined query", _field(rewritten, "query")
    if status == "unclear":
        return "Refined query", "None yet. It is not clear which trip this is."
    text = _field(guard, "clean_text")
    return "Sent as it is" if text == clean_message(utterance) else "Refined query", text


def _ask(utterance: str) -> None:
    """Put one message in the queue. The page shows it at once and _work runs it."""
    st.session_state.queue = [*st.session_state.queue, {"utterance": utterance}]


def _located(fix: dict[str, Any] | None) -> None:
    """Queue the rest of the turn that waited, with the browser's reply, or without one when the user moved on."""
    waiting, st.session_state.waiting = st.session_state.waiting, None
    device = device_place(fix)
    job = {
        "utterance": waiting["turn"]["utterance"],
        "turn": waiting["turn"],
        "waited": waiting["state"],
        "device": device,
        "no_location": fix is not None and device is None,
        "steps": waiting.get("steps", []),
        "seconds": waiting.get("seconds", 0.0),
    }
    st.session_state.queue = [job, *st.session_state.queue]


def _prepare(job: dict[str, Any]) -> None:
    """Work out what the graph gets, once. A job that is interrupted by a click runs again from here."""
    if "turn" in job:
        return
    utterance = job["utterance"]
    trip = _pick_trip(utterance)
    message, whole = (trip, True) if trip else _answer(utterance)
    request = message if whole else _with_pending(message)
    known = None if whole else st.session_state.known
    if trip:
        # The trip is settled, so what was asked before it was chosen does not count.
        st.session_state.clarify_count = 0
        st.session_state.asked = []
    job["turn"] = {"utterance": utterance, "message": message, "request": request, "known": known}
    job["payload"] = {
        "request_id": f"r_{uuid4().hex[:8]}",
        "utterance": request,
        "latest": message,
        # A place or a trip picked from a list is already clear, so it needs no reading of the conversation.
        "history": list(st.session_state.turns) if message == utterance else None,
        # 1 is a follow-up on a known trip, as in the label guide and D1. Never 2.
        "turn_index": 0 if known is None else 1,
        "known": known,
        "clarify_count": st.session_state.clarify_count,
        "asked": list(st.session_state.asked),
        "picked": list(st.session_state.picked),
        "seen": list(st.session_state.seen),
        "revision_count": 0,
        "locatable": not _sample(),
    }


def _input_html(shown: tuple[str, str]) -> str:
    label, text = shown
    return f"<p class='rc-input'><b>{escape(label)}</b>{escape(text)}</p>"


def _open_turn(job: dict[str, Any]) -> tuple[Any, Any]:
    """Show the message and the first step at once. Returns the places the live parts are drawn in."""
    with st.chat_message("user"):
        st.markdown(job["utterance"])
        refined = st.empty()
    with st.chat_message("assistant"):
        board = st.empty()
    first = doing("guard_in") if "waited" not in job else doing("maps")
    board.markdown(board_html(job.get("steps", []), first), unsafe_allow_html=True)
    return refined, board


def _work(job: dict[str, Any], refined: Any, board: Any) -> None:
    """Run the first job in the queue and draw each step as it starts and as it finishes."""
    _prepare(job)
    steps: list[str] = list(job.get("steps", []))
    seen: dict[str, Any] = dict(job.get("waited") or {})
    started = time.perf_counter()

    def on_step(stage: str, update: dict[str, Any] | None) -> None:
        if update is None:
            board.markdown(board_html(steps, doing(stage)), unsafe_allow_html=True)
            return
        seen.update(update)
        line = done(stage, update)
        if line:
            steps.append(line)
        board.markdown(board_html(steps, None), unsafe_allow_html=True)
        if stage == "rewrite":
            shown = _planner_input(seen, job["utterance"])
            if shown:
                refined.markdown(_input_html(shown), unsafe_allow_html=True)

    nodes: dict[str, Any] = {}
    if _own_keys() and _sample():
        from routechat.stubs import stub_nodes

        nodes = {"nodes": stub_nodes()}
    with keys.visitor_keys(st.session_state.get("own_keys") or {}) if _own_keys() else nullcontext():
        if "waited" in job:
            state = resume(job["waited"], job["device"], **nodes, on_step=on_step)
        else:
            state = invoke(job["payload"], **nodes, on_step=on_step)
    seconds = job.get("seconds", 0.0) + time.perf_counter() - started
    # Nothing below draws, so a click cannot stop it halfway.
    st.session_state.queue = st.session_state.queue[1:]
    st.session_state.no_location = job.get("no_location", False)
    if "waited" not in job and wants_device(state):
        st.session_state.waiting = {"state": state, "turn": job["turn"], "steps": steps, "seconds": seconds}
        return
    _remember(state, **job["turn"])
    st.session_state.history[-1]["steps"] = (steps, seconds)


def _remember(state: dict[str, Any], *, utterance: str, message: str, request: str, known: Any) -> None:
    """Keep the reply, the constraints, and the map state of a finished turn."""
    request_id = state.get("request_id")
    final = state.get("final")
    answer = _field(final, "text", "I couldn't finish that trip. Please try again in a minute.")
    st.session_state.history.append({"role": "user", "content": utterance, "input": _planner_input(state, utterance)})
    st.session_state.history.append({"role": "assistant", "content": answer})
    rewritten = state.get("rewrite")
    status = _field(rewritten, "status")
    if status == "ready":
        request = _field(rewritten, "query")
    if is_new_trip(state):
        # Questions asked about another trip do not count against this one.
        st.session_state.clarify_count = 0
        st.session_state.asked = []
        st.session_state.picked = []
    # A message the guard stopped never reaches the rewriter, now or later.
    if _field(state.get("guard"), "decision") == "allow":
        st.session_state.turns = [
            *st.session_state.turns,
            {"user": clean_message(message), "query": clean_message(request), "assistant": answer},
        ]
        st.session_state.full = clean_message(request) if status == "ready" or known is None else None
    constraints = state.get("constraints")
    action = _field(state.get("classification"), "route_action")
    if action is None and status == "unclear":
        action = "clarify"
        st.session_state.trips = list(_field(rewritten, "options"))
    report = state.get("report")
    escalated = action == "escalate"
    # A clarify may carry a half-read trip. The pending message is what the answer is joined to.
    # A trip passed to a person is finished here, so the next message starts a new one.
    if constraints is not None and action != "clarify":
        st.session_state.known = None if escalated else constraints
    asked_before = st.session_state.clarify_count > 0
    if action == "clarify":
        st.session_state.clarify_count += 1
        st.session_state.pending = request
    elif action is not None:
        # The verifier asks about a trip that is already known, so its answer is a follow-up on that trip.
        asks = not escalated and _field(report, "decision") == "clarify"
        st.session_state.clarify_count = st.session_state.clarify_count + 1 if asks else 0
        st.session_state.pending = None
    key = question_key(state)
    counted = st.session_state.clarify_count > 0 and key is not None and not escalated
    st.session_state.asked = [*st.session_state.asked, key] if counted else []
    if counted and _field(report, "handoff_reason") == "place_unclear":
        st.session_state.options = list(state.get("places") or [])
    if escalated or _field(report, "decision") == "handoff" or state.get("handoff") is not None:
        reason = "still_unclear" if asked_before else "needs_a_person"
        ticket = state.get("handoff") or {
            "request_id": request_id,
            "reason": reason if escalated else _field(report, "handoff_reason") or "handoff",
            "redacted_request": mask_for_log(request),
        }
        if hasattr(ticket, "model_dump"):
            ticket = ticket.model_dump()
        append_handoff(ticket)
        st.session_state.tickets = [*st.session_state.tickets, ticket.get("request_id")]
    if _field(report, "decision") == "accept":
        ends = [
            place for place in state.get("places") or []
            if _field(place, "role") != "stop" and _field(place, "source") != "device"
        ]
        st.session_state.seen = [*ends, *st.session_state.seen][:_SEEN]
    # graph.invoke already wrote this request's line; the app adds no second copy.
    st.session_state.last = state


def _on_phone() -> bool:
    """True when the browser says it is a phone. The server cannot see the width of the screen."""
    return "Mobi" in str(st.context.headers.get("User-Agent") or "")


def _view(coords: list[list[float]]) -> pdk.ViewState:
    lons = [point[0] for point in coords]
    lats = [point[1] for point in coords]
    span = max(max(lons) - min(lons), max(lats) - min(lats), 0.01)
    # One step wider than a square map, so both pins stay inside the short stage.
    zoom = 12 if span < 0.03 else 11 if span < 0.08 else 10
    longitude, latitude = sum(lons) / len(lons), sum(lats) / len(lats)
    # A long trip: centre on the box around it and zoom out until a 400 by 180 px stage holds it.
    # The map on a phone is narrower than that.
    room = 240 if _on_phone() else 400
    wide = max(max(lons) - min(lons), 0.01)
    tall = max((max(lats) - min(lats)) / math.cos(math.radians(latitude)), 0.01)
    fit = math.log2(360 / 512 * min(room / wide, 180 / tall))
    if fit < zoom and span >= 0.2:
        zoom = max(fit, 2)
        longitude, latitude = (max(lons) + min(lons)) / 2, (max(lats) + min(lats)) / 2
    return pdk.ViewState(
        longitude=longitude,
        latitude=latitude,
        zoom=zoom,
        pitch=0,
        bearing=0,
    )


def _pretty_id(raw: str) -> str:
    parts = str(raw).replace("_", " ").split(":")
    return " · ".join(part.strip().title() for part in parts if part.strip())


def _words(raw: str) -> str:
    """fuel_or_charging as Fuel or charging."""
    text = raw.replace("_", " ").strip()
    return text[:1].upper() + text[1:]


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


def _map_view() -> str:
    """The chosen basemap. The switcher sits under the map, so the choice is read from the session."""
    view = st.session_state.get("map_view")
    return view if view in _MAP_VIEWS else _DEFAULT_VIEW


def _view_switcher() -> None:
    st.segmented_control(
        "Map view",
        list(_MAP_VIEWS),
        default=_DEFAULT_VIEW,
        required=True,
        key="map_view",
        label_visibility="collapsed",
    )


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
        hours_line = _hours_line(place)
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
        _base_map()
        return
    focus = coords or [point["coordinates"] for point in points]
    catalog = _shown_catalog(sponsored)
    layers.extend(_catalog_layers(catalog))
    deck = pdk.Deck(
        layers=layers,
        initial_view_state=_view(focus),
        map_provider="carto",
        map_style=_MAP_VIEWS[_map_view()],
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
    # A row that wraps, so the switcher is never cut off on a narrow screen.
    row = st.container(
        key="mapfoot", horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"
    )
    with row:
        st.markdown(_legend_html(has_sponsor), unsafe_allow_html=True, width="content")
        _view_switcher()
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


def _hours_line(place: Any) -> str:
    """Opening hours, when they are known. Where the user is has none."""
    if _field(place, "source") == "device":
        return "From your device"
    return str(_field(place, "opening_hours") or "Hours not confirmed")


def _places(state: dict[str, Any]) -> None:
    places = list(state.get("places") or [])
    if not places:
        return
    rows = []
    for place in places:
        hours_line = _hours_line(place)
        detour = _field(place, "detour_km")
        extra = f" · detour {detour:.1f} km" if detour is not None and detour >= 0.05 else ""
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
            f"<p>{escape(tidy_evidence(str(_field(check, 'evidence') or '')))}</p>"
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
                _words(str(_field(sponsored, "category") or "Place")),
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


def _tickets() -> list[dict[str, Any]]:
    """The support queue. On a server shared by visitors, only the tickets of this visit."""
    tickets = read_handoffs()
    if not keys.from_visitors():
        return tickets
    return [ticket for ticket in tickets if ticket.get("request_id") in st.session_state.tickets]


def _support() -> None:
    tickets = _tickets()
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
            f"<b>{escape(_words(str(reason)))}</b>"
            f"<p>{escape(str(text))}</p>"
            "</div>"
        )
    st.markdown(
        "<section class='rc-support'><div class='rc-section'>Support</div>" + "".join(blocks) + "</section>",
        unsafe_allow_html=True,
    )


def _base_map() -> None:
    """Downtown Toronto, shown whenever there is no route or place to draw."""
    catalog = _shown_catalog()
    deck = pdk.Deck(
        layers=_catalog_layers(catalog),
        initial_view_state=pdk.ViewState(latitude=43.6532, longitude=-79.3832, zoom=12, pitch=0),
        map_provider="carto",
        map_style=_MAP_VIEWS[_map_view()],
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


def _location() -> None:
    """Ask the browser where the user is while a trip waits for it, and say so when there was no answer."""
    waiting = st.session_state.waiting
    if waiting is None:
        if st.session_state.no_location:
            st.caption("I couldn't get your location. Allow it in the browser, or type a start.")
        return
    with st.chat_message("user"):
        st.markdown(waiting["turn"]["utterance"])
    with st.chat_message("assistant"):
        st.markdown(
            board_html(waiting.get("steps", []), "Finding your location. Your browser may ask you to allow it"),
            unsafe_allow_html=True,
        )
    fix = locate.browser_fix(waiting["state"]["request_id"])
    if fix is not None:
        _located(fix)
        st.rerun()


def _close_keys() -> None:
    st.session_state.keys_open = False


@st.dialog("Your keys", on_dismiss=_close_keys)
def _keys_dialog() -> None:
    """Take this visitor's keys, check them with each provider, and keep them for this visit."""
    st.markdown(
        "RouteChat uses your own accounts, so your usage is charged to you. Your keys stay in this server's "
        "memory for this visit. They are not saved or logged, and other visitors cannot see them. "
        "Refreshing the page clears them."
    )
    st.markdown(
        "Get a key from [OpenRouter](https://openrouter.ai/settings/keys) or "
        "[OpenAI](https://platform.openai.com/api-keys), "
        "[Mapbox](https://console.mapbox.com/account/access-tokens/) and "
        "[GraphHopper](https://graphhopper.com/dashboard/). Set a spending limit on the model key."
    )
    with st.form("keys_form", border=False):
        model = st.text_input("OpenRouter or OpenAI key", type="password", autocomplete="off")
        mapbox = st.text_input("Mapbox token", type="password", autocomplete="off")
        graphhopper = st.text_input("GraphHopper key", type="password", autocomplete="off")
        sent = st.form_submit_button("Check and use", type="primary", width="stretch")
    if sent:
        model, mapbox, graphhopper = model.strip(), mapbox.strip(), graphhopper.strip()
        if not (model and mapbox and graphhopper):
            st.error("All three keys are needed.")
        else:
            given = {keys.model_key_name(model): model, "MAPBOX_TOKEN": mapbox, "GRAPHHOPPER_KEY": graphhopper}
            with st.spinner("Checking your keys"):
                found = keys.problems(given)
            if found:
                st.error("\n\n".join(found))
            else:
                _use_keys(given)
                st.rerun()
    if st.session_state.own_keys and st.button("Remove my keys", key="remove_keys", width="stretch"):
        _use_keys({})
        st.rerun()


def _use_keys(given: dict[str, str]) -> None:
    """Change this visit's keys. The thread starts again, so a sample trip never mixes with a real one."""
    _new_chat()
    _close_keys()
    st.session_state.own_keys = given


def _conversation(sample: bool) -> tuple[str | None, Any]:
    """The chat. Returns the new message, if any, and where the running turn is drawn."""
    live = None
    with st.container(key="rail"):
        st.markdown("<div class='rc-panel-title'>Conversation</div>", unsafe_allow_html=True)
        if _own_keys():
            label = "Your keys" if st.session_state.get("own_keys") else "Add your keys"
            if st.button(label, icon=":material/key:", key="open_keys", width="stretch"):
                st.session_state.keys_open = True
        # One block inside the scroll area: the area shows its end, so a new step is always in view.
        with st.container(key="thread"), st.container(key="messages"):
            queue = st.session_state.queue
            if not st.session_state.history and st.session_state.waiting is None and not queue:
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
                        if message.get("input"):
                            st.markdown(_input_html(message["input"]), unsafe_allow_html=True)
                        if message.get("steps"):
                            st.markdown(summary_html(*message["steps"]), unsafe_allow_html=True)
            _location()
            if queue:
                live = _open_turn(queue[0])
        _support()
        st.markdown("<div class='rc-field-label'>Trip request</div>", unsafe_allow_html=True)
        # Without keys every request would get the sample ride, so only its button is on.
        locked = _own_keys() and sample
        prompt = st.chat_input(
            "Add your keys to plan a trip" if locked else "Where are you heading?",
            disabled=locked,
            key=f"composer-{st.session_state.composer_epoch}",
        )
    return prompt, live


def main() -> None:
    _configure()
    st.set_page_config(page_title="RouteChat", page_icon="🗺️", layout="wide")
    st.markdown(_CSS, unsafe_allow_html=True)
    _init()
    sample = _sample()
    _topbar(sample)
    rail, stage = st.columns([0.28, 0.72], gap=16, vertical_alignment="top")
    with rail:
        prompt, live = _conversation(sample)
    if st.session_state.keys_open:
        _keys_dialog()
    if prompt:
        if st.session_state.waiting is not None:
            # The user wrote instead of allowing it, so that turn ends with its question.
            _located(None)
        _ask(prompt)
        st.rerun()
    with stage:
        _stage(st.session_state.last)
    if live is not None:
        # Last, so the message and the map are on screen while the steps run.
        _work(st.session_state.queue[0], *live)
        st.rerun()


if __name__ == "__main__":
    main()
