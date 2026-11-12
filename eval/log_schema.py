# Owner: Person E (WP10 evaluation / WP11 economics), Team Rosedale, MMAI 894.
# Only Person E edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP10 or WP11.
# Written for this project with AI assistance (see eval/README.md, "AI use"); no code copied from other sources.
"""Log fields that WP10 (evaluation) and WP11 (cost model) read.

Agreed at M1 together with the metric definitions. WP7 (routechat/logging.py) writes the lines;
this module only describes and checks them, so evaluation fails loudly if a field is missing.

Two kinds of JSON line, one file per run (logs/<run_id>.jsonl):
  1. a stage line: one per stage per request (the format already in the work packages, A2 and WP7)
  2. a request line: one per request, written by WP7 after guard_out, with stage = "request"
Only masked text is ever logged (P-06). No line holds the raw utterance.
"""
from __future__ import annotations

# Stage names come from the orchestrator (WP0) so the two can never drift apart.
from routechat.graph import NODE_ORDER

STAGES = NODE_ORDER

# 1. Stage line. The first nine fields are exactly the ones in the work packages (A2, WP7 example).
STAGE_REQUIRED = {
    "request_id": str,
    "stage": str,          # one of STAGES
    "latency_ms": (int, float),
    "tokens_in": int,
    "tokens_out": int,
    "cost_usd": (int, float),   # model cost only; API calls are priced by the cost model from api_calls
    "api_calls": dict,     # e.g. {"searchbox": 3, "graphhopper": 1, "directions": 1}; {} when none
    "decision": str,       # the stage's own decision, e.g. allow, plan, ok, accept, revise, approved
    "ts": str,             # ISO 8601 UTC
}
# Added for WP10/WP11. Optional on stages that do not use them.
STAGE_OPTIONAL = {
    "run_id": str,         # same value on every line of one evaluation run
    "model": str,          # model name for the three model stages; backend name for the classifier
    "prompt_version": str, # e.g. extractor_v1
    "error_type": str,     # exception type only, as graph._log_failure already writes it; never raw text
}
API_NAMES = ("geocoding", "searchbox", "searchbox_category", "graphhopper", "directions", "openai", "openrouter")

# 2. Request line (stage == "request"), one per request.
OUTCOMES = ("accept", "clarify", "refuse", "handoff", "fixed_reply")
REQUEST_REQUIRED = {
    "request_id": str,
    "stage": str,              # always "request"
    "run_id": str,
    "git_commit": str,         # short hash, so every number traces back to code
    "dataset": str,            # D1, D2, D3, D5 or "live" for app traffic
    "item_id": str,            # id of the dataset record; "" for live traffic
    "turn_index": int,         # 0 or 1
    "outcome": str,            # one of OUTCOMES (see outcome_from_state)
    "route_action": str,       # from RouteClassification, "" if the guard stopped the request
    "primary_mode": str,
    "revision_count": int,     # 0, 1 or 2: the graph counts a second revise as 2 (it does not loop again)
    "checks": list,            # [{"id": "highways", "status": "met", "hard": true}, ...] from VerificationReport
    "must_mention": list,      # from VerificationReport
    "mentioned_ids": list,     # from ReplyDraft
    "used_fallback_template": bool,  # from FinalAnswer
    "approved": bool,          # from FinalAnswer
    "sponsored_shown": bool,   # VerificationReport.sponsored is not null and was shown
    "handoff_reason": str,     # "" when no handoff
    "stopped_at": str,         # first stage that ended the normal path (guard_in, classify, extract, verify) or ""
    "latency_ms_total": (int, float),
    "cost_usd_total": (int, float),
    "ts": str,
}


def outcome_from_state(state: dict) -> str:
    """Map a final TripState to one outcome label, matching D2's expected outcome field.

    Follows the routing table in WP0: the first stage that leaves the normal path decides.
    """
    guard = state.get("guard") or {}
    g = guard.get("decision")
    if g in ("refuse", "clarify", "fixed_reply"):
        return g
    cls = state.get("classification") or {}
    a = cls.get("route_action")
    if a == "refuse":
        return "refuse"
    # A second clarify counts as handoff. The graph rewrites it to route_action = "escalate" before the
    # writer runs and does not create a HandoffTicket, so we read route_action, not the ticket.
    if a == "escalate":
        return "handoff"
    if a == "clarify":
        return "handoff" if state.get("clarify_count", 0) >= 1 else "clarify"
    con = state.get("constraints") or {}
    if con.get("missing_field") or con.get("too_many_stops"):
        return "clarify"
    rep = state.get("report") or {}
    if rep.get("decision") == "handoff" or state.get("handoff"):
        return "handoff"
    return "accept"


def _check(line: dict, spec: dict, required: bool) -> list[str]:
    problems = []
    for k, typ in spec.items():
        if k not in line:
            if required:
                problems.append(f"missing {k}")
            continue
        if line[k] is None and not required:
            continue  # optional fields may be null, e.g. error_type when no exception was raised
        if not isinstance(line[k], typ) or (typ is int and isinstance(line[k], bool)):
            problems.append(f"{k} has type {type(line[k]).__name__}")
    return problems


def validate_line(line: dict) -> list[str]:
    """Return a list of problems; an empty list means the line is fine."""
    if line.get("stage") == "request":
        p = _check(line, REQUEST_REQUIRED, True)
        if line.get("outcome") not in OUTCOMES:
            p.append(f"outcome {line.get('outcome')!r} not in {OUTCOMES}")
        return p
    p = _check(line, STAGE_REQUIRED, True) + _check(line, STAGE_OPTIONAL, False)
    if line.get("stage") not in STAGES:
        p.append(f"stage {line.get('stage')!r} not in {STAGES}")
    for name in (line.get("api_calls") or {}):
        if name not in API_NAMES:
            p.append(f"unknown api name {name!r}")
    return p
