# Owner: WP8 data generation lead, Team Rosedale, MMAI 894.
# Only the WP8 owner edits this file. Other packages may import it but should not change it;
# AI coding tools must not edit this file unless they are working on WP8.
# Written for this project with AI assistance; no code copied from other sources.
"""WP8 tests. Ids follow the handbook convention T-<package>-<nn> (package 8 = data generation).
All tests run offline and free (mock generator)."""
import csv
import json
import random
from collections import Counter

import sys
from pathlib import Path

import pytest

# data_gen/ sits at the repo root (like eval/), so add the root to the path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_gen import datasets, pipeline as P, sampling, spec


# ---------------------------------------------------------------- spec
def test_T_8_01_quotas_sum_to_one():
    for w in (spec.ACTION_WEIGHTS, spec.MODE_WEIGHTS, spec.DIFFICULTY_WEIGHTS, spec.TURN_WEIGHTS):
        assert abs(sum(w.values()) - 1) < 1e-9
    assert abs(sum(spec.REFUSAL_SPLIT.values()) - 1) < 1e-9
    assert sum(spec.HANDWRITTEN_PER_ACTION.values()) == 150
    assert spec.TEST_N == spec.TEST_PER_ACTION * 7


def test_T_8_02_agreed_vocabulary_changes():
    assert "multimodal" in spec.PRIMARY_MODES and "hike" in spec.PRIMARY_MODES
    assert ("transit" in spec.PRIMARY_MODES) == (spec.TRANSIT_POLICY == "named_mode")
    assert ("out_of_scope_mode" in spec.REFUSAL_REASONS) == (spec.TRANSIT_POLICY == "out_of_scope_mode")
    if spec.TRANSIT_POLICY == "out_of_scope_mode":
        assert spec.REFUSAL_REASONS.index("out_of_scope_b2b") < spec.REFUSAL_REASONS.index("out_of_scope_mode")
    assert spec.USE_CASE_BY_ID[20].modes == frozenset({"car"})              # motorcycle -> car
    if spec.TRANSIT_POLICY == "out_of_scope_mode":
        for i in (10, 18):                                                   # out-of-schema rows
            assert spec.USE_CASE_BY_ID[i].refusals == frozenset({"out_of_scope_mode"})
    assert all(u.id in spec.USE_CASE_BY_ID for u in spec.USE_CASES) and len(spec.USE_CASES) == 31


def test_T_8_03_label_consistency_rules():
    ok = dict(route_action="plan", primary_mode="bike", needs_live_data=False, clarify_field="none", refusal_reason="none")
    assert spec.validate_labels(ok) == []
    assert spec.validate_labels({**ok, "route_action": "clarify"})                 # clarify needs a field
    assert spec.validate_labels({**ok, "route_action": "refuse"})                  # refuse needs a reason
    assert spec.validate_labels({**ok, "refusal_reason": "data_request"})          # reason without refuse
    assert bool(spec.validate_labels({**ok, "primary_mode": "transit"})) == (spec.TRANSIT_POLICY != "named_mode")


# ---------------------------------------------------------------- sampling
def test_T_8_04_every_quota_is_reachable():
    rep = sampling.reachability_report()
    assert rep["ok"], rep["max_marginal_error"]
    for m in rep["marginals"]:
        assert abs(m["target"] - m["achieved"]) < 1e-3, m


def test_T_8_05_jobs_match_quotas_and_are_valid():
    jobs = sampling.build_jobs(2200, seed=1)
    assert len(jobs) == 2200
    cov = sampling.coverage_table(jobs)
    for a, w in spec.ACTION_WEIGHTS.items():
        assert abs(cov["route_action"].get(a, 0) / 2200 - w) < 0.01, a
    for m, w in spec.MODE_WEIGHTS.items():
        assert abs(cov["primary_mode"].get(m, 0) / 2200 - w) < 0.01, m
    assert all(not spec.validate_labels(j["labels"]) for j in jobs)
    # `run` is only reachable through rows that allow it: the running loop (19) and replan-after-disruption (21)
    assert {j["use_case_id"] for j in jobs if j["labels"]["primary_mode"] == "run"} <= {19, 21}
    assert all(j["labels"]["primary_mode"] == "unspecified" for j in jobs if j["labels"]["refusal_reason"] == "out_of_scope_mode")
    assert all(j["known"] for j in jobs if j["turn"] == 1) and not any(j["known"] for j in jobs if j["turn"] == 0)


def test_T_8_06_jobs_are_reproducible():
    a, b = sampling.build_jobs(300, seed=9), sampling.build_jobs(300, seed=9)
    assert a == b and a != sampling.build_jobs(300, seed=10)


# ---------------------------------------------------------------- prompts, slots, qc
def test_T_8_07_prompt_has_no_unfilled_tokens():
    job = sampling.build_jobs(50, seed=2)[0]
    text = " ".join(m["content"] for m in P.build_messages(job))
    assert "<<" not in text and job["labels"]["route_action"] in text


def test_T_8_08_placeholders_filled_and_detected():
    job = sampling.build_jobs(5, seed=3)[0]
    slots = P.make_slots(job)
    assert P.fill_placeholders("from {landmark} to {landmark_2}, call {phone}", slots).count("{") == 0
    assert P.leftover_placeholders("meet at {home_adress}") == ["home_adress"]
    assert slots["email"].endswith("@example.com") and slots["landmark"] != slots["landmark_2"]


def _rec(raw, **kw):
    job = sampling.build_jobs(40, seed=4)[0]
    r = {"id": "x", "labels": job["labels"], "type": job["type"], "key": job["key"], "generator": "llm",
         "raw_message": raw, "utterance": P.fill_placeholders(raw, P.make_slots(job)), "parse_error": None}
    r.update(kw)
    return r


@pytest.mark.parametrize("raw,reason", [
    ("bike", "length"),
    ("pick me up at {home_adress} please and thanks", "unfilled_placeholder"),
    ("call me at 416-555-0199 about the bike route to {landmark}", "raw_pii"),
    ("mail me at sam@gmail.com the bike route to {landmark}", "raw_pii"),
    ("As an AI language model I cannot but here is a route to {landmark}", "boilerplate"),
    ("persona says I should bike from {landmark} to {landmark_2}", "meta_leak"),
    ("take the subway from {landmark} to {landmark_2} then walk", "unexpected_transit_mention"),
])
def test_T_8_09_qc_rejects_with_reason(raw, reason):
    rec = _rec(raw)
    rec["type"], rec["key"] = "clean", "plan"
    assert reason in P.qc(rec)


def test_T_8_10_qc_passes_clean_and_allows_transit_where_designed():
    rec = _rec("bike route from {landmark} to {landmark_2}, avoid highways")
    rec["type"], rec["key"] = "clean", "plan"
    assert P.qc(rec) == []
    rec2 = _rec("when is the last subway from {station}")
    rec2["type"], rec2["key"] = "clean", "refuse/out_of_scope_mode"
    assert "unexpected_transit_mention" not in P.qc(rec2)


def test_T_8_11_near_duplicates_keep_first():
    texts = ["plan a bike route from the cn tower to union station", "plan a bike route from the cn tower to union station!",
             "is the trail near banff open today"]
    assert P.near_duplicates(texts) == [1]


def test_T_8_12_parse_error_is_a_reject():
    assert P.qc({"parse_error": "no JSON object found"}) == ["parse_error"]
    obj, err = P.extract_json("```json\n{\"message\": \"hi there you\"}\n```")
    assert obj == {"message": "hi there you"} and err is None
    assert P.extract_json("Sure! here is nothing")[0] is None


# ---------------------------------------------------------------- end to end (offline)
@pytest.fixture(scope="module")
def d1(tmp_path_factory):
    out = tmp_path_factory.mktemp("d1")
    cfg = P.Cfg(out_dir=str(out), n_jobs=1800, cache_dir=str(out / "cache"))
    P.stage_generate(cfg, P.make_client(cfg, offline=True), verbose=False)
    return out


def test_T_8_13_generate_keeps_rejects_and_flags_disagreements(d1):
    m = json.loads((d1 / "run_manifest.json").read_text())["generate"]
    assert m["n_kept"] + m["n_rejected"] == m["n_generated"] == 1800
    assert len(P.read_jsonl(d1 / "rejected.jsonl")) == m["n_rejected"]
    assert all(r["qc_reasons"] for r in P.read_jsonl(d1 / "rejected.jsonl"))
    rows = list(csv.DictReader(open(d1 / "review.csv", encoding="utf-8")))
    assert len(rows) == m["n_for_review"] > 0 and rows[0]["decision"] == ""
    assert (d1 / "diversity.json").exists()


def test_T_8_14_review_keep_fix_reject(d1):
    recs = P.read_jsonl(d1 / "candidates.jsonl")
    sub = recs[:3]
    P.export_review_csv(sub, d1 / "rv.csv")
    rows = list(csv.DictReader(open(d1 / "rv.csv", encoding="utf-8")))
    rows[0]["decision"], rows[1]["decision"], rows[2]["decision"] = "keep", "reject", "fix"
    rows[2]["edited_utterance"] = "completely rewritten message by a human"
    with open(d1 / "rv.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    out, stats = P.apply_review(sub, d1 / "rv.csv")
    assert stats == {"kept": 1, "rejected": 1, "fixed": 1} and len(out) == 2
    assert out[1]["utterance"].startswith("completely rewritten") and out[1]["human_reviewed"]
    rows[2]["corrected_labels"] = "route_action=clarify;clarify_field=none"
    with open(d1 / "rv.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    with pytest.raises(ValueError):                 # a human fix may not create an invalid label set
        P.apply_review(sub, d1 / "rv.csv")


def test_T_8_15_split_is_balanced_leak_free_and_grouped(d1, tmp_path):
    sheet = P.make_handwriting_sheet(tmp_path / "hw.csv")
    rows = list(csv.DictReader(open(sheet, encoding="utf-8")))
    assert len(rows) == 150 and Counter(r["route_action"] for r in rows) == Counter(spec.HANDWRITTEN_PER_ACTION)
    for i, r in enumerate(rows):
        r["message"] = f"my own words about trip number {i} in {r['city']} for {r['route_action']}"
    with open(tmp_path / "hw.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    rep = P.stage_split(d1, handwritten_csv=str(tmp_path / "hw.csv"), verbose=False)
    assert rep["cross_split_overlap_is_zero"]
    tr, va, te = (P.read_jsonl(d1 / f"{s}.jsonl") for s in ("train", "val", "test"))
    assert Counter(r["route_action"] for r in te) == {a: spec.TEST_PER_ACTION for a in spec.ROUTE_ACTIONS}
    assert sum(r["source"] == "human" for r in te) == 150
    g = [{r["group"] for r in s} for s in (tr, va, te)]
    assert not (g[0] & g[2]) and not (g[1] & g[2]) and not (g[0] & g[1])


def test_T_8_16_freeze_requires_review_and_detects_tampering(d1):
    with pytest.raises(SystemExit):                                  # generated test rows are not reviewed yet
        P.stage_freeze(d1)
    test = P.read_jsonl(d1 / "test.jsonl")
    for r in test:
        r["human_reviewed"] = True
    P.write_jsonl(d1 / "test.jsonl", test)
    h = P.stage_freeze(d1)
    assert len(h) == 64 and P.stage_verify(d1)
    with pytest.raises(SystemExit):                                  # cannot re-split a frozen test set
        P.stage_split(d1, verbose=False)
    test[0]["utterance"] += " tampered"
    P.write_jsonl(d1 / "test.jsonl", test)
    assert not P.stage_verify(d1)


def test_T_8_17_kappa(tmp_path):
    cols = ["id", "utterance", "known"] + [f"{p}_{f}" for p in "ab" for f in spec.LABEL_FIELDS]
    rng = random.Random(0)
    with open(tmp_path / "k.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for i in range(60):
            a = rng.choice(spec.ROUTE_ACTIONS)
            row = {"id": i, "utterance": "u", "known": ""}
            for p in "ab":
                row.update({f"{p}_{k}": "x" for k in spec.LABEL_FIELDS})
                row[f"{p}_route_action"] = a
            w.writerow(row)
    assert P.kappa_report(tmp_path / "k.csv")["route_action"] == 1.0


# ---------------------------------------------------------------- D2-D4
@pytest.fixture(scope="module")
def ds(tmp_path_factory):
    import os
    cwd = os.getcwd()
    d = tmp_path_factory.mktemp("ds")
    os.chdir(d)
    (d / "eval/datasets").mkdir(parents=True); (d / "data").mkdir()
    try:
        datasets.main(["all", "--offline"])
        yield d
    finally:
        os.chdir(cwd)


def test_T_8_18_d2_structure(ds):
    recs = P.read_jsonl(ds / "eval/datasets/d2_end_to_end.jsonl")
    assert len(recs) == 120 and Counter(r["expected_outcome"] for r in recs) == datasets.D2_OUTCOMES
    for r in recs:
        if r["expected_outcome"] == "clarify":
            cs = r["gold_constraint_set"]
            assert cs["missing_field"] in ("origin", "destination", "stops")
            assert not cs[cs["missing_field"]] if cs["missing_field"] != "stops" else not cs["stops"]
        if r["expected_outcome"] == "refuse":
            assert r["refusal_reason"] in spec.REFUSAL_REASONS[1:] and r["gold_constraint_set"] is None
        if r["expected_outcome"] == "handoff":
            assert r["handoff_reason"]
        cs = r["gold_constraint_set"]
        assert cs is None or cs["mode"] in (None, "walk", "bike", "hike", "run", "car")
        assert (r["message"] is None) == (r["source"] == "handwritten_todo")
    assert sum(r["source"] == "handwritten_todo" for r in recs) >= 40
    assert (ds / "data_gen/review/d2_coords_verification.csv").exists()


def test_T_8_19_d2_handoff_deadline_is_really_infeasible(ds):
    for r in P.read_jsonl(ds / "eval/datasets/d2_end_to_end.jsonl"):
        if r.get("handoff_reason") == "infeasible_deadline":
            assert r["straight_line_km"] >= 15 and r["gold_constraint_set"]["max_duration_min"] == 10
        if r.get("handoff_reason") == "too_many_stops":
            assert len(r["gold_constraint_set"]["stops"]) >= 8 and r["gold_constraint_set"]["too_many_stops"]


def test_T_8_20_d4_sponsors_are_fake_and_in_range(ds):
    sp = json.loads((ds / "data/sponsors.json").read_text())
    assert len(sp) == 40 and len({s["name"] for s in sp}) == 40
    assert all(3.0 <= s["rating"] <= 5.0 and s["is_fake"] for s in sp)
    assert not any(b in s["name"].lower() for s in sp for b in datasets.REAL_BRAND_BLOCKLIST)


def test_T_8_21_d3_counts(ds):
    d3 = P.read_jsonl(ds / "eval/datasets/d3_adversarial.jsonl")
    assert len(d3) == 120 and Counter(r["family"] for r in d3) == {f: 20 for f in datasets.FAMILIES}
    assert sum(r["two_turn"] for r in d3) == 10 and all(len(r["turns"]) == (2 if r["two_turn"] else 1) for r in d3)
    assert all(r["expected"]["must_not"] for r in d3)


def test_T_8_22_no_real_personal_data_in_outputs(ds, d1):
    import re
    blob = " ".join(r["utterance"] for r in P.read_jsonl(d1 / "candidates.jsonl"))
    assert all(e.endswith("@example.com") for e in re.findall(r"[\w.+-]+@[\w.-]+", blob))


def test_T_8_23_d4_sponsors_sit_near_places_that_fit_their_category(ds):
    sp = json.loads((ds / "data/sponsors.json").read_text())
    assert all(s["anchor_kind"] in datasets.SPONSOR_ANCHOR_KINDS[s["category"]] for s in sp)
    assert len({s["city"] for s in sp}) == 4                                  # all four cities represented
    assert max(Counter(s["anchor_place"] for s in sp).values()) <= 2          # anchors are spread out
    assert all(100 <= s["distance_from_anchor_m"] <= 500 for s in sp)


# ----- repo-contract tests (skipped when routechat/schemas.py is not reachable) -----------------
from data_gen import contracts

needs_schemas = pytest.mark.skipif(not contracts.available(), reason="routechat/schemas.py not found")


@needs_schemas
def test_T_8_24_d1_jobs_validate_against_frozen_schemas():
    for j in sampling.build_jobs(600, seed=11):
        contracts.validate_labels(j["labels"])
        if j["known"]:
            contracts.validate_user_turn({"utterance": "x", "turn_index": 1, "known": j["known"]})


@needs_schemas
def test_T_8_25_d2_d3_have_evaluator_fields_and_valid_gold(tmp_path):
    datasets.build_d2(offline=True, out_dir=str(tmp_path), review_dir=str(tmp_path / "rev"))
    sp = tmp_path / "sp.json"
    sp.write_text("[]")
    datasets.build_d3(offline=True, out_dir=str(tmp_path), sponsor_path=str(sp))
    d2 = [json.loads(x) for x in (tmp_path / "d2_end_to_end.jsonl").read_text().splitlines()]
    d3 = [json.loads(x) for x in (tmp_path / "d3_adversarial.jsonl").read_text().splitlines()]
    for r in d2:
        assert r["item_id"] and r["expected_outcome"] in ("accept", "clarify", "refuse", "handoff")
        if r["gold_constraints"]:
            contracts.validate_constraints(r["gold_constraints"])
    for r in d3:
        assert r["item_id"] and r["attack_type"] and r["expected_outcome"] in ("refuse", "handoff", "accept")
        assert r["turn_index"] in (0, 1) and (r["known"] is not None) == (r["turn_index"] == 1)
        if r["known"]:
            contracts.validate_constraints(r["known"])


def test_T_8_26_injection_block_composer_varies_delimiter_and_placement():
    rng = random.Random(3)
    msgs = [datasets.compose_injection(rng, "fake_system_block") for _ in range(200)]
    assert any("SYSTEM" in m for m in msgs) and any("ADMIN" in m for m in msgs) and any("[INST]" in m for m in msgs)
    assert len({m.split()[0] for m in msgs}) > 3                      # placement/delimiter variety
    subtle = [datasets.compose_injection(rng, "subtle_authority_note") for _ in range(50)]
    assert not any(t in m for m in subtle for t in ("SYSTEM:", "ADMIN:", "[INST]"))   # no special token


def test_T_8_27_multimodal_never_involves_transit():
    for u in spec.USE_CASES:
        if "multimodal" in u.modes and spec.TRANSIT_POLICY == "out_of_scope_mode":
            assert u.id == 30 and "transit" not in u.name.lower()


def test_T_8_28_sponsor_names_are_city_flavoured_and_ratings_clustered(ds):
    sp = json.loads((ds / "data/sponsors.json").read_text())
    flavoured = [x for x in sp if any(w in x["name"] for w in datasets.CITY_FLAVOUR[x["city"]])]
    assert len(flavoured) >= len(sp) // 3                                   # a good share carry local flavour
    assert all(w not in x["name"] for x in sp for c, ws in datasets.CITY_FLAVOUR.items() if c != x["city"] for w in ws)
    r = sorted(x["rating"] for x in sp)
    assert all(3.0 <= v <= 5.0 for v in r)
    assert 3 <= sum(v < 4.0 for v in r) <= 8                                # a few below the 4.0 floor
    assert sum(4.0 <= v <= 4.6 for v in r) >= int(0.6 * len(r))             # most clustered mid-high
    assert 3 <= sum(v >= 4.7 for v in r) <= 8
