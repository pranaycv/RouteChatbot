"""WP7. Asks the browser for the device location, once, while a trip waits for it.

Streamlit has no call for this, so a small component runs
navigator.geolocation.getCurrentPosition. The browser shows its own permission
question. It works on https and on localhost only; anywhere else, and when the
user blocks it, the reply is an error and the app asks for a start instead.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

# The browser's timeout does not count while its permission question is open, so there is a second one.
_JS = """
export default function ({ data, setStateValue }) {
  let done = false;
  const reply = (fix) => { if (!done) { done = true; clearTimeout(timer); setStateValue("fix", fix); } };
  const timer = setTimeout(() => reply({ error: "timeout" }), data.wait_ms);
  if (!window.isSecureContext || !navigator.geolocation) {
    reply({ error: "unsupported" });
  } else {
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => reply({ lat: coords.latitude, lon: coords.longitude, accuracy: coords.accuracy }),
      (failure) => reply({ error: failure.code === 1 ? "denied" : "unavailable" }),
      { enableHighAccuracy: true, timeout: data.fix_ms, maximumAge: data.max_age_ms },
    );
  }
  return () => { done = true; clearTimeout(timer); };
}
"""

_TIMING = {"wait_ms": 30_000, "fix_ms": 10_000, "max_age_ms": 60_000}
_component = None


def browser_fix(request_id: str) -> dict[str, Any] | None:
    """The browser's reply for this request: a point, an error, or None while it is still working."""
    global _component
    if _component is None:
        _component = st.components.v2.component("routechat_device_location", js=_JS)
    result = _component(key=f"device-{request_id}", data=_TIMING, on_fix_change=lambda: None)
    return result.get("fix")
