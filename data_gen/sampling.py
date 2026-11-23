# Owner: WP8 data generation lead, Team Rosedale, MMAI 894.
# Only the WP8 owner edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP8.
# Written for this project with AI assistance; no code copied from other sources.
"""Quota solver and job sampler for D1.

Why IPF: the handbook gives four independent marginals (action, mode, difficulty,
turn) but many combinations are impossible (a run request cannot be a compare_modes
case; out_of_scope_mode has no usable mode). Iterative proportional fitting finds the
joint table closest to our priors that still satisfies every marginal, and tells us
honestly when a quota is unreachable.

Pipeline: spec -> prior tensor -> IPF -> integer cell counts -> concrete jobs.
"""
from __future__ import annotations

import csv
import random
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

import numpy as np

from . import spec

KEYS = spec.ACTION_KEYS
DIFFS = spec.DIFFICULTIES
MODES = spec.PRIMARY_MODES
TURNS = (0, 1)


# ----------------------------------------------------------------------------
# Places
# ----------------------------------------------------------------------------
class PlaceBook:
    """Real public landmarks from places.csv (names + approx coordinates)."""

    KIND_POOLS = {
        "landmark": ["landmark", "museum", "market", "venue", "campus"],
        "trailhead": ["trailhead", "park"],
        "station": ["station"],
        "park": ["park", "trailhead"],
        "airport": ["airport"],
    }

    def __init__(self, path=None):
        path = path or spec.HERE / "places.csv"
        self.rows: List[dict] = []
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                r["lat"], r["lon"] = float(r["lat"]), float(r["lon"])
                self.rows.append(r)
        self.by_city: Dict[str, List[dict]] = defaultdict(list)
        for r in self.rows:
            self.by_city[r["city"]].append(r)

    def pick(self, city: str, pool: str, rng: random.Random, exclude=()) -> dict:
        kinds = self.KIND_POOLS.get(pool, [pool])
        cands = [r for r in self.by_city[city] if r["kind"] in kinds and r["name"] not in exclude]
        if not cands:   # e.g. a city with no 'station' row: fall back to any place
            cands = [r for r in self.by_city[city] if r["name"] not in exclude] or self.by_city[city]
        return rng.choice(cands)


# ----------------------------------------------------------------------------
# Candidate rows
# ----------------------------------------------------------------------------
def candidate_rows(key: str, mode: str) -> List[spec.UseCase]:
    action, reason = spec.split_key(key)
    out = []
    for u in spec.USE_CASES:
        if action not in u.actions:
            continue
        if action == "refuse" and reason not in u.refusals:
            continue
        if mode == "unspecified":
            if not u.unspecified_ok:
                continue
        elif mode not in u.modes:
            continue
        out.append(u)
    return out


# ----------------------------------------------------------------------------
# IPF
# ----------------------------------------------------------------------------
def targets() -> Dict[str, np.ndarray]:
    kw = spec.key_weights()
    return {
        "key": np.array([kw[k] for k in KEYS]),
        "diff": np.array([spec.DIFFICULTY_WEIGHTS[d] for d in DIFFS]),
        "mode": np.array([spec.MODE_WEIGHTS[m] for m in MODES]),
        "turn": np.array([spec.TURN_WEIGHTS[t] for t in TURNS]),
    }


def prior_tensor() -> np.ndarray:
    P = np.zeros((len(KEYS), len(DIFFS), len(MODES), len(TURNS)))
    for ki, k in enumerate(KEYS):
        for di, d in enumerate(DIFFS):
            for mi, m in enumerate(MODES):
                if m not in spec.MODE_COMPAT[k] or not candidate_rows(k, m):
                    continue
                for ti, t in enumerate(TURNS):
                    tf = 1.0 if t == 0 else spec.TURN_FACTOR[k] * spec.TURN_WEIGHTS[1] / spec.TURN_WEIGHTS[0]
                    P[ki, di, mi, ti] = spec.DIFF_PRIOR[k][d] * spec.MODE_WEIGHTS[m] * tf
    return P


def ipf(P: np.ndarray, tg: Dict[str, np.ndarray], iters: int = 2000, tol: float = 1e-7) -> Tuple[np.ndarray, float]:
    Q = P.copy()
    axes = {"key": 0, "diff": 1, "mode": 2, "turn": 3}
    err = 1.0
    for _ in range(iters):
        for name, ax in axes.items():
            other = tuple(i for i in range(4) if i != ax)
            cur = Q.sum(axis=other)
            scale = np.divide(tg[name], cur, out=np.zeros_like(cur), where=cur > 0)
            shape = [1, 1, 1, 1]
            shape[ax] = -1
            Q = Q * scale.reshape(shape)
        err = max(np.abs(Q.sum(axis=tuple(i for i in range(4) if i != ax)) - tg[name]).max()
                  for name, ax in axes.items())
        if err < tol:
            break
    return Q / Q.sum(), float(err)


def solve() -> Tuple[np.ndarray, float]:
    return ipf(prior_tensor(), targets())


def integer_counts(Q: np.ndarray, n: int) -> np.ndarray:
    """Largest-remainder rounding so counts add to exactly n."""
    flat = Q.ravel() * n
    base = np.floor(flat).astype(int)
    short = n - base.sum()
    if short > 0:
        order = np.argsort(-(flat - base))
        base[order[:short]] += 1
    return base.reshape(Q.shape)


def reachability_report(n: int = 2200) -> dict:
    """Which quotas can the structure actually deliver? Used by `plan` CLI and tests."""
    Q, err = solve()
    tg = targets()
    marg = {
        "key": Q.sum(axis=(1, 2, 3)), "diff": Q.sum(axis=(0, 2, 3)),
        "mode": Q.sum(axis=(0, 1, 3)), "turn": Q.sum(axis=(0, 1, 2)),
    }
    labels = {"key": KEYS, "diff": DIFFS, "mode": MODES, "turn": list(TURNS)}
    rows = []
    for name in ("key", "diff", "mode", "turn"):
        for i, lab in enumerate(labels[name]):
            rows.append({"axis": name, "value": lab, "target": round(float(tg[name][i]), 4),
                         "achieved": round(float(marg[name][i]), 4)})
    # realised use-case share vs nominal weight
    share: Counter = Counter()
    for ki, k in enumerate(KEYS):
        for mi, m in enumerate(MODES):
            mass = Q[ki, :, mi, :].sum()
            cands = candidate_rows(k, m)
            action = spec.split_key(k)[0]
            tot = sum(u.weight_for(action) for u in cands) or 1
            for u in cands:
                share[u.id] += mass * u.weight_for(action) / tot
    return {"max_marginal_error": err, "marginals": rows,
            "use_case_share": {i: round(float(s), 4) for i, s in sorted(share.items())},
            "ok": err < 1e-3}


# ----------------------------------------------------------------------------
# Jobs
# ----------------------------------------------------------------------------
LIVE_LOOKUPS = {"traffic", "closure", "trail_status"}


def _persona(row: spec.UseCase, key: str, rng: random.Random) -> str:
    w = {p: 1.0 for p in spec.PERSONA_IDS}
    for p, b in spec.PERSONA_AFFINITY.get(row.id, {}).items():
        w[p] += b
    if key == "escalate":
        w["angry_user"] += 3
    return rng.choices(list(w), weights=list(w.values()))[0]


def _make_known(row, key, mode, city, clarify_field, book: PlaceBook, rng) -> dict:
    """What the assistant already understood from turn 0: a FULL ConstraintSet (frozen v1 schema)."""
    from data_gen import contracts
    o = book.pick(city, "landmark", rng)
    d = book.pick(city, "landmark", rng, exclude=(o["name"],))
    known = contracts.empty_constraints(origin=o["name"], destination=d["name"], mode=mode)
    if rng.random() < 0.4:
        known["stops"].append(contracts.stop(
            book.pick(city, "landmark", rng, exclude=(o["name"], d["name"]))["name"], True, 1))
    if mode in ("bike", "car") and rng.random() < 0.5:
        known["avoid"].append("highways")
    if mode == "bike" and rng.random() < 0.5:
        known["prefer"].append("bike_lanes")
    if clarify_field in ("origin", "destination"):
        known[clarify_field] = None
        known["missing_field"] = clarify_field
    elif clarify_field == "stops":
        known["stops"] = []
    return known


def build_jobs(n: int, seed: int = 42, book: PlaceBook | None = None) -> List[dict]:
    """Turn the solved joint table into n concrete, fully specified generation jobs."""
    book = book or PlaceBook()
    rng = random.Random(seed)
    Q, err = solve()
    counts = integer_counts(Q, n)
    jobs: List[dict] = []
    for (ki, di, mi, ti), c in np.ndenumerate(counts):
        key, diff, mode, turn = KEYS[ki], DIFFS[di], MODES[mi], TURNS[ti]
        action, reason = spec.split_key(key)
        for _ in range(int(c)):
            cands = candidate_rows(key, mode)
            row = rng.choices(cands, weights=[u.weight_for(action) for u in cands])[0]
            jtype = rng.choice(spec.difficulty_types(key, diff))
            # --- labels ---
            clarify_field = "none"
            if action == "clarify":
                allowed = [f for f in row.clarify_fields]
                w = {"origin": .4, "destination": .4, "stops": .2}
                clarify_field = rng.choices(allowed, weights=[w[f] for f in allowed])[0]
            lookup = rng.choice(row.lookups) if (action == "lookup_only" and row.lookups) else None
            if action == "replan":
                live = True
            elif action == "lookup_only":
                live = (lookup in LIVE_LOOKUPS) if lookup else True
            elif action == "compare_modes":
                live = rng.random() < max(row.live_p, 0.5) or row.id == 16
            elif action == "plan":
                live = rng.random() < row.live_p or jtype == "multi_task" or row.id == 16
            else:
                live = False
            city = rng.choices(list(row.cities or spec.CITIES),
                               weights=[spec.CITIES[c_] for c_ in (row.cities or spec.CITIES)])[0]
            known = _make_known(row, key, mode, city, clarify_field, book, rng) if turn == 1 else None
            jobs.append({
                "use_case_id": row.id, "use_case": row.name, "use_case_desc": row.description,
                "key": key, "difficulty": diff, "type": jtype, "turn": turn, "city": city,
                "persona": _persona(row, key, rng), "lookup_kind": lookup, "known": known,
                "labels": {"route_action": action, "primary_mode": mode, "needs_live_data": bool(live),
                           "clarify_field": clarify_field, "refusal_reason": reason},
                "seed": rng.randint(1, 1_000_000),
            })
    rng.shuffle(jobs)
    nm, nt = len(spec.GENERATOR_MODELS), len(spec.TEMPERATURES)
    for i, j in enumerate(jobs):
        j["id"] = f"d1_{i:05d}"
        j["model"] = spec.GENERATOR_MODELS[i % nm]
        j["temperature"] = spec.TEMPERATURES[(i // nm) % nt]
        j["top_p"] = spec.TOP_P
        bad = spec.validate_labels(j["labels"])
        assert not bad, (j, bad)
    return jobs


def coverage_table(jobs: List[dict]) -> Dict[str, Dict[str, int]]:
    """Counts used in the report's coverage table."""
    out: Dict[str, Counter] = {k: Counter() for k in
                               ("route_action", "primary_mode", "difficulty", "turn", "refusal_reason",
                                "use_case_id", "persona", "type")}
    for j in jobs:
        for f in ("route_action", "primary_mode", "refusal_reason"):
            out[f][j["labels"][f]] += 1
        for f in ("difficulty", "turn", "use_case_id", "persona", "type"):
            out[f][j[f]] += 1
    return {k: dict(v) for k, v in out.items()}
