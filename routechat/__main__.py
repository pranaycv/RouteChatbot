"""Run the orchestrator on the handbook's running example.

The Streamlit app belongs to WP7. Until it is merged, this command is the
runnable entry point.
"""

import os

from routechat.graph import invoke
from routechat.stubs import RUNNING_UTTERANCE


def main() -> None:
    os.environ.setdefault("STUBS", "1")
    state = invoke(
        {
            "request_id": "r_0193",
            "utterance": RUNNING_UTTERANCE,
            "turn_index": 0,
            "known": None,
            "clarify_count": 0,
            "revision_count": 0,
        }
    )
    final = state["final"]
    print(final.text if not isinstance(final, dict) else final["text"])


if __name__ == "__main__":
    main()
