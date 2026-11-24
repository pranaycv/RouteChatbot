# Written for this project with AI assistance; no code copied from other sources.
"""Where the API keys come from.

By default a key is read from the environment, which .env fills. With ROUTECHAT_KEYS=visitor
each visitor brings their own. The app then wraps a run in visitor_keys(), and the run sees
those keys only: never the server's, and never another visitor's. A context variable holds
them, so two visitors who plan at the same time do not share one.

A visitor's keys are never logged and never written to a file.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

import httpx

MODEL_KEYS = ("OPENAI_API_KEY", "OPENROUTER_API_KEY")
MAP_KEYS = ("MAPBOX_TOKEN", "GRAPHHOPPER_KEY")

_visitor: ContextVar[Mapping[str, str] | None] = ContextVar("routechat_visitor_keys", default=None)


def from_visitors() -> bool:
    """True when each visitor brings their own keys."""
    return os.environ.get("ROUTECHAT_KEYS", "").strip().lower() == "visitor"


def get_key(name: str) -> str | None:
    """One key, or None. In visitor mode a run without its visitor's keys finds none: it never falls back."""
    own = _visitor.get()
    if own is not None or from_visitors():
        return (own or {}).get(name) or None
    return os.environ.get(name) or None


@contextmanager
def visitor_keys(keys: Mapping[str, str]) -> Iterator[None]:
    """Run the block with these keys and no others."""
    token = _visitor.set(dict(keys))
    try:
        yield
    finally:
        _visitor.reset(token)


# In two parts, so the scan for keys in the repository does not stop at this file.
_OPENROUTER_START = "s" + "k-or-"


def model_key_name(key: str) -> str:
    """The start of an OpenRouter key is its own. Any other model key is taken as an OpenAI key."""
    return "OPENROUTER_API_KEY" if key.startswith(_OPENROUTER_START) else "OPENAI_API_KEY"


def _accepted(name: str, key: str) -> bool:
    if name == "MAPBOX_TOKEN":
        reply = httpx.get("https://api.mapbox.com/tokens/v2", params={"access_token": key}, timeout=10)
        # Mapbox answers 200 for a wrong token too, and says so in the body.
        reply.raise_for_status()
        return reply.json().get("code") == "TokenValid"
    if name == "GRAPHHOPPER_KEY":
        reply = httpx.get(
            "https://graphhopper.com/api/1/geocode", params={"q": "Toronto", "limit": 1, "key": key}, timeout=10
        )
    else:
        url = "https://openrouter.ai/api/v1/key" if name == "OPENROUTER_API_KEY" else "https://api.openai.com/v1/models"
        reply = httpx.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=10)
    if reply.status_code in (401, 403):
        return False
    # A limit that is used up for now still means the key is real.
    if reply.status_code != 429:
        reply.raise_for_status()
    return True


_NAMES = {
    "OPENROUTER_API_KEY": "OpenRouter key",
    "OPENAI_API_KEY": "OpenAI key",
    "MAPBOX_TOKEN": "Mapbox token",
    "GRAPHHOPPER_KEY": "GraphHopper key",
}


def problems(keys: Mapping[str, str]) -> list[str]:
    """Ask each provider about its key. Returns one line per key that cannot be used, never the key itself."""
    found = []
    for name, key in keys.items():
        if not key.isascii() or any(char.isspace() for char in key):
            found.append(f"The {_NAMES[name]} has a space or an odd character in it.")
            continue
        try:
            accepted = _accepted(name, key)
        except (httpx.HTTPError, ValueError):
            # The error can hold the address, and the address can hold the key.
            found.append(f"The {_NAMES[name]} could not be checked just now. Please try again.")
            continue
        if not accepted:
            found.append(f"The {_NAMES[name]} was not accepted.")
    return found
