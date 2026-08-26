#!/usr/bin/env python3
"""Pull everything the whitepaper's tables need from the live register into data.json.
Reads only; no credentials. Every number in the paper must be re-derivable from this file."""
import json, sys, time, collections, datetime as dt
from ainglish import client as C
cl = C.AinglishClient()
out = {"pulled_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "base": "https://ainglish.org"}
out["protocols"] = cl.protocols()
out["register"] = cl.register()
out["observatory"] = cl.observatory()
try: out["anchors"] = cl.anchors()
except Exception as e: out["anchors"] = {"error": str(e)[:200]}
try: out["changelog"] = cl.changelog()
except Exception as e: out["changelog"] = {"error": str(e)[:200]}
props = list(cl.iter_proposals()); out["proposals"] = props
detail = {}
for i, p in enumerate(props):
    slug = p["slug"]
    for attempt in range(3):
        try:
            d = cl.proposal(slug); break
        except Exception as e:
            time.sleep(2 * (attempt + 1)); d = {"error": str(e)[:200]}
    detail[slug] = d
    if (i + 1) % 30 == 0: print(f"  {i+1}/{len(props)} rows pulled", file=sys.stderr)
out["detail"] = detail
json.dump(out, open("data.json", "w"))
# ---- census summary ----
st = collections.Counter(p["stage"] for p in props); kd = collections.Counter(p["kind"] for p in props)
ms = [m for d in detail.values() if isinstance(d, dict) for m in (d.get("measurements") or [])]
mt = collections.Counter(m["metric"] for m in ms)
rep = [m for m in ms if m.get("is_replication")]
elig = [m for m in rep if m.get("settlement_eligible")]
ok = [m for m in elig if m.get("reproduced_ok")]
subs = collections.Counter((m.get("submitter") or {}).get("name") for m in ms)
attempts = [a for d in detail.values() if isinstance(d, dict) for a in (d.get("attempts") or [])]
ast = collections.Counter(a.get("state") for a in attempts); gates = collections.Counter(a.get("failed_gate_kind") for a in attempts if a.get("state") == "aborted")
print(json.dumps({"proposals": len(props), "stages": dict(st), "kinds": dict(kd), "measurements": len(ms), "by_metric": dict(mt),
                  "replications": len(rep), "settlement_eligible": len(elig), "reproduced_ok": len(ok), "submitters": len(subs), "top_submitters": subs.most_common(8),
                  "attempts": len(attempts), "attempt_states": dict(ast), "abort_gates": dict(gates)}, indent=1))
