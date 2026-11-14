"""Exposed software assertion for the next SDK release; no network or model calls.

Run with tools/rehearse_release.py against the previous published SDK and the
candidate wheel. This checks newly shipped behavior, not protocol activation.
"""
import copy

from ainglish import paired_study as paired, preservation, preservation_inventory as inventory

assert preservation.minimum_per_cell(16) == 126
manifest = {
    "preservation_analysis": "exact-binomial-preservation-v1",
    "readers": [{"name": "a"}, {"name": "b"}],
    "settlement_strata": [{"id": "form-a", "weight": 1}, {"id": "form-b", "weight": 1}],
    "preservation_inventory": {
        "kind": "ainglish.preservation-inventory.v1",
        "readers": [{"name": "a", "lineage": "x"}, {"name": "b", "lineage": "y"}],
        "forms": ["form-a", "form-b"],
        "error_endpoints": [{"id": arm, "family": "error", "arm": arm, "scoring_key_sha256": "a"*64}
                            for arm in ("english", "ainglish")],
        "sampling": {"unit": "world", "cluster_field": "world_id", "per_cell": 512},
    },
}
assert inventory.check(manifest)["problems"] == []
assert inventory.check(manifest)["family"]["family_size"] == 16
bad = copy.deepcopy(manifest)
bad["settlement_strata"][0]["weight"] = 10**400
assert [p["code"] for p in inventory.check(bad)["problems"]] == ["manifest_strata"]
bad = copy.deepcopy(manifest)
bad["preservation_inventory"]["sampling"]["per_cell"] = 2**53
assert [p["code"] for p in inventory.check(bad)["problems"]] == ["per_cell_type"]

plan = {
    "kind": "ainglish.separate-call-pairs.v1", "proposal_id": "synthetic-release-control",
    "seed": 17, "population_scope": "Exposed software controls only.",
    "exposure": "isolated-stateless-calls", "assignment": "both-arms-per-reader-pair",
    "point_weighting": "equal-pair-equal-reader", "missing_policy": "abort-study",
    "reuse_policy": "fresh-call-per-occurrence", "resampling_unit": "pair",
    "readers": [{"id": "stub", "instrument_ref": "synthetic-not-a-model"}],
    "pairs": [{"id": "pair-%d" % i, "family": "family-%d" % i,
               "twins": [{"id": "twin-%d" % j, "world_id": "world-%d-%d" % (i,j),
                          "english": "E message-%d-%d" % (i,j), "ainglish": "A message-%d-%d" % (i,j),
                          "question": "Choose.", "options": ["x","y"], "answer": ["x","y"][j]}
                         for j in range(2)]} for i in range(2)],
}
for i,j in ((0,0),(0,1),(1,1)):
    twin=plan["pairs"][i]["twins"][j];twin["ainglish"]=twin["english"]
events=[]
result=paired.execute(plan,lambda *_: "x",registration={"reference":"synthetic-only","plan_sha256":paired.validate(plan)},record=events.append)
assert len(events)==16 and result["report"]["separate_calls"]==8
assert result["report"]["identical_arm_twin_count"]==3
assert result["report"]["pair_accuracy_delta_pp"]==0
assert all(not any(row["correct"].values()) for row in result["report"]["pair_scores"])
assert paired.replay(plan,result)==result["report"]
assert result["registration_verified"] is False
assert result["report"]["governance_effect"]=="report_only"
print("preservation arithmetic/inventory and report-only separate-call replay verified")
