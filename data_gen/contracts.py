# Owner: WP8 data generation lead, Team Rosedale, MMAI 894.
# Only the WP8 owner edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP8.
# Written for this project with AI assistance; no code copied from other sources.
"""Bridge between WP8 data and the team's frozen v1 schemas (routechat/schemas.py).

Owner: WP8 (data generation). Read-only with respect to routechat/: we never edit the
schemas, we only validate our data against them so a contract change shows up as a test
failure here instead of a silent mismatch in the evaluator.

Schema lookup order: (1) the real package when running inside the repo, (2) the file
named by $ROUTECHAT_SCHEMAS, (3) None -> validation helpers fall back to a clear error.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Optional

_SCHEMA_FILE = Path(__file__).resolve().parents[1] / "routechat" / "schemas.py"


def _load():
    try:                                              # inside the repo
        from routechat import schemas                 # type: ignore
        return schemas
    except Exception:
        pass
    for cand in (os.environ.get("ROUTECHAT_SCHEMAS"), str(_SCHEMA_FILE)):
        if cand and Path(cand).exists():
            spec = importlib.util.spec_from_file_location("_routechat_schemas", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)              # schemas.py only needs pydantic
            return mod
    return None


schemas = _load()


def available() -> bool:
    return schemas is not None


def _need():
    if schemas is None:
        raise RuntimeError("routechat/schemas.py not found; set ROUTECHAT_SCHEMAS or run inside the repo")
    return schemas


def empty_constraints(**over: Any) -> dict:
    """A complete ConstraintSet dict with every required field present (all 'nothing known')."""
    base = dict(origin=None, destination=None, stops=[], mode="unspecified", modes_to_compare=[],
                compare_threshold_min=None, avoid=[], prefer=[], minimize_elevation=False,
                check_closures=False, max_duration_min=None, max_distance_km=None, loop=False,
                target_distance_km=None, depart_at=None, arrive_by=None, required_accessibility=False,
                ambiguities=[], missing_field=None, too_many_stops=False)
    base.update(over)
    return base


def stop(query: str, required: bool = True, order: Optional[int] = None, category: Optional[str] = None) -> dict:
    return {"query": query, "category": category, "required": required, "order": order}


def validate_constraints(d: dict) -> dict:
    """Return the validated dict (round-tripped); raises pydantic.ValidationError if invalid."""
    return _need().ConstraintSet(**d).model_dump()


def validate_user_turn(d: dict) -> dict:
    return _need().UserTurn(**d).model_dump()


# Proposed contract extensions that are NOT yet in the frozen v1 schema (awaiting team decision).
# Validation substitutes a legal value so every OTHER field is still checked against the real schema.
PROPOSED_REFUSAL_REASONS = {"out_of_scope_mode"}


def validate_labels(labels: dict) -> dict:
    """Check the five classifier labels against RouteClassification.
    `out_of_scope_mode` is accepted as a proposed extension (see above); returned dict keeps it."""
    probe = dict(labels, parse_error=False)
    proposed = probe.get("refusal_reason") in PROPOSED_REFUSAL_REASONS
    if proposed:
        probe["refusal_reason"] = "none"
    out = _need().RouteClassification(**probe).model_dump()
    if proposed:
        out["refusal_reason"] = labels["refusal_reason"]
    return out
