# Owner: Person A (WP6 and WP7), Team Rosedale, MMAI 894.
# Only Person A edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP6 or WP7.
# Written for this project with AI assistance; no code copied from other sources.
"""The verifier's evidence as the writer, the app and the output guard read it."""

from __future__ import annotations

import pytest

from routechat.evidence import evidence_numbers, tidy_evidence


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("4220.55 m; allowed range 4500 to 5500 m.", "4.22 km; allowed range 4.5 to 5.5 km."),
        ("8.96243 m climb per km (maximum 10).", "9 m climb per km (maximum 10)."),
        ("10.3 m climb per km (maximum 10).", "10.3 m climb per km (maximum 10)."),
        ("679 s; allowed range 0 to 2100 s.", "11 minutes; allowed range 0 to 35 minutes."),
        ("5540 m; allowed range 4500 to 5500 m.", "5.54 km; allowed range 4.5 to 5.5 km."),
        ("72% on bike network (min 60%)", "72% on bike network (min 60%)"),
        ("Closure data is unavailable.", "Closure data is unavailable."),
    ],
)
def test_evidence_is_rounded_and_text_is_left_alone(raw: str, shown: str):
    assert tidy_evidence(raw) == shown
    assert tidy_evidence(shown) == shown


def test_only_measurements_in_the_evidence_are_allowed():
    numbers = evidence_numbers("4220.55 m; allowed range 4500 to 5500 m.")
    assert numbers == {"km": {4.22, 4.2, 4.5, 5.5}, "min": set(), "m": set()}
    assert evidence_numbers("66.4% on the bike network (minimum 60.0%).") == {"km": set(), "min": set(), "m": set()}
