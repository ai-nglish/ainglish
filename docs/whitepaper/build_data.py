#!/usr/bin/env python3
"""Pull everything the whitepaper's tables need from the live register (reads only, no
credentials) into two files beside this script:

  data.json      the register snapshot: protocols, register, observatory, anchors, changelog,
                 every proposal's list row and detail record (measurements, attempts, consensus)
  campaign.json  for each manifest hash listed in campaign-rows.json: the served measurement
                 record, its full manifest, and the frozen item set the manifest pins — the
                 envelope's metadata and the item bytes, so the renderer can re-verify both the
                 manifest and the item set against their content addresses

FAIL-CLOSED. A partial census is worse than none: any fetch that fails after retries, any
proposal whose detail record is missing, any campaign manifest that does not hash to its own
manifest_hash, or any item set that does not hash to its manifest's items_sha256 aborts the run
with exit status 2 before either file is written.

    build_data.py                  pull both files
    build_data.py --campaign-only  pull campaign.json only (manifests and item-set envelopes are
                                   content-addressed and immutable, so this pull's time does not
                                   change the numbers; data.json is left untouched)

Compress with `gzip -n` (no timestamp) so the pinned digest in SHA256SUMS is reproducible."""
import json, sys, time, hashlib, os, collections, datetime as dt, urllib.request
from ainglish import client as C

HERE = os.path.dirname(os.path.abspath(__file__))
CAMPAIGN_ONLY = "--campaign-only" in sys.argv
cl = C.AinglishClient()
failures = []


def canonical_sha256(value):
    # The SDK's manifest convention (panel.py): sorted keys, compact separators, UTF-8 as is.
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def fetch(label, fn, tries=4):
    last = None
    for attempt in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - every failure is recorded and fails the run
            last = e
            time.sleep(2 * (attempt + 1))
    failures.append("%s: %s: %s" % (label, type(last).__name__, str(last)[:200]))
    return None


def abort_if_failed(stage):
    if failures:
        print("ABORT (%s): %d failure(s); nothing written" % (stage, len(failures)), file=sys.stderr)
        for f in failures:
            print("  " + f, file=sys.stderr)
        sys.exit(2)


def fetch_json_url(label, url):
    def go():
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.load(r)
    return fetch(label, go)


# ---------------------------------------------------------------- register snapshot
if not CAMPAIGN_ONLY:
    out = {"pulled_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "base": "https://ainglish.org"}
    out["protocols"] = fetch("protocols", cl.protocols)
    out["register"] = fetch("register", cl.register)
    out["observatory"] = fetch("observatory", cl.observatory)
    out["anchors"] = fetch("anchors", cl.anchors)
    out["changelog"] = fetch("changelog", cl.changelog)
    props = fetch("proposals (list)", lambda: list(cl.iter_proposals()))
    abort_if_failed("envelopes")
    out["proposals"] = props
    detail = {}
    for i, p in enumerate(props):
        d = fetch("proposal %s" % p["slug"], lambda s=p["slug"]: cl.proposal(s))
        if d is not None:
            detail[p["slug"]] = d
        if (i + 1) % 30 == 0:
            print("  %d/%d rows pulled" % (i + 1, len(props)), file=sys.stderr)
    abort_if_failed("proposal details")
    if set(detail) != {p["slug"] for p in props}:
        failures.append("detail keyset differs from the proposal list: missing=%s extra=%s" % (
            sorted({p["slug"] for p in props} - set(detail))[:5], sorted(set(detail) - {p["slug"] for p in props})[:5]))
    abort_if_failed("keyset")
    bad = [s for s, d in detail.items() if not isinstance(d, dict) or "error" in d or "measurements" not in d]
    if bad:
        failures.append("detail records without a measurements field: %s" % bad[:5])
    abort_if_failed("detail shape")
    out["detail"] = detail

# ---------------------------------------------------------------- campaign rows
ids = json.load(open(os.path.join(HERE, "campaign-rows.json")))["rows"]
campaign = {}
for h in ids:
    rec = fetch("measurement %s" % h[:12], lambda h=h: cl.measurement(h))
    if rec is None:
        continue
    man = rec.get("manifest")
    if not isinstance(man, dict):
        failures.append("measurement %s: no manifest served" % h[:12]); continue
    if canonical_sha256(man) != h:
        failures.append("measurement %s: served manifest does not hash to its manifest_hash" % h[:12]); continue
    url = man.get("items_url")
    env = fetch_json_url("items %s" % h[:12], url) if url else None
    if env is None:
        if url:
            continue
        failures.append("measurement %s: manifest has no items_url" % h[:12]); continue
    items = env["items"] if isinstance(env, dict) else env
    if canonical_sha256(items) != man.get("items_sha256"):
        failures.append("measurement %s: item set at %s does not hash to the manifest's items_sha256" % (h[:12], url)); continue
    campaign[h] = {"record": {k: v for k, v in rec.items() if k != "manifest"}, "manifest": man,
                   "items_url": url, "items_meta": ({k: v for k, v in env.items() if k != "items"} if isinstance(env, dict) else {"_bare_list": True}),
                   "items": items}
abort_if_failed("campaign rows")
if set(campaign) != set(ids):
    failures.append("campaign keyset differs from campaign-rows.json")
abort_if_failed("campaign keyset")

# ---------------------------------------------------------------- write (only now)
if not CAMPAIGN_ONLY:
    json.dump(out, open(os.path.join(HERE, "data.json"), "w"))
json.dump({"pulled_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "rows": campaign},
          open(os.path.join(HERE, "campaign.json"), "w"))
print("campaign.json: %d rows, every manifest and item set verified against its digest" % len(campaign))
if not CAMPAIGN_ONLY:
    ms = [m for d in out["detail"].values() for m in (d.get("measurements") or [])]
    attempts = [a for d in out["detail"].values() for a in (d.get("attempts") or [])]
    print(json.dumps({"proposals": len(out["proposals"]), "stages": dict(collections.Counter(p["stage"] for p in out["proposals"])),
                      "measurements": len(ms), "replication_filings": sum(1 for m in ms if m.get("is_replication")),
                      "settlement_eligible": sum(1 for m in ms if m.get("is_replication") and m.get("settlement_eligible")),
                      "attempts": len(attempts), "preregistered": sum(1 for a in attempts if not a.get("backfilled")),
                      "backfilled": sum(1 for a in attempts if a.get("backfilled"))}, indent=1))
