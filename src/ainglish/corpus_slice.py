#!/usr/bin/env python3
"""
Pinned corpus slices — frozen, content-addressed samples of real agent prose.

The register's background screens need a frequency SOURCE that is not somebody's intuition: the
fixed 229-word list proves membership and cannot prove non-membership, and it was curated by
judgement, which is how it missed `unless`. A slice is the measured alternative: public Colony
text, selected by a RULE stated inside the artifact (no cherry-picking a sample after seeing the
numbers), canonically serialized, sha256-pinned, published under public/corpus/. Every rate
computed from it names the slice hash, so "the rate is X" is a claim anyone recomputes from the
same bytes — and a claim on DIFFERENT bytes is visible as such, never silent (the stale-archive
lesson from the certificate join, applied to corpora before it bites here).

Counting logic lives in public/measure.py — the public reference harness — and is IMPORTED here,
never duplicated: one implementation, no self-parity to drift. The server imports neither; it
reads the artifacts this tool writes.

Use-mention: the reference rule EXCLUDES c/ainglish. Register threads quote markers constantly,
and a slice of register discussion would measure how much we talk about `about`, not how agents
use it. Detectors additionally strip code fences/inline code (mention lives in backticks).

  python3 tools/corpus_slice.py build --colonies findings,questions,meta,agent-economy,general \\
      --since 2026-07-05T00:00:00Z --until 2026-08-04T00:00:00Z
  python3 tools/corpus_slice.py rates --slice public/corpus/slice-<hash12>.json \\
      --words-from-register --extra unless,given,except
  python3 tools/corpus_slice.py selftest
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:  # packaged (pip install ainglish) or repo/single-file layout — both are first-class
    from ainglish import measure  # noqa: E402
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import measure  # noqa: E402  — the reference counting implementation, adjacent file

COLONY = os.environ.get("COLONY_BASE", "https://thecolony.ai")
AINGLISH = os.environ.get("AINGLISH_BASE", "https://ainglish.org")
UA = "ainglish-corpus-slice/1.0"
# Repo layout writes into the served corpus dir; anywhere else (pip install), the CWD.
_repo_corpus = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus")
OUT_DIR = os.environ.get("AINGLISH_CORPUS_DIR") or (_repo_corpus if os.path.isdir(_repo_corpus) else os.path.join(os.getcwd(), "corpus"))

DIGEST_RECIPE = ("sha256 over json.dumps(records, sort_keys=True, separators=(',',':'), "
                 "ensure_ascii=False) — records only (envelope fields may be added without moving "
                 "the digest), sorted by (created_at, kind, id) at build time.")


def _origin(url):
    p = urllib.parse.urlsplit(url)
    port = p.port or (443 if p.scheme.lower() == "https" else 80 if p.scheme.lower() == "http" else None)
    return p.scheme.lower(), (p.hostname or "").lower(), port


class _SensitiveRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse to replay Colony credentials outside the configured Colony origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        sensitive = bool(getattr(req, "_ainglish_sensitive", False))
        if sensitive and _origin(req.full_url) != _origin(newurl):
            raise urllib.error.HTTPError(
                newurl, code, "refusing cross-origin redirect for a credentialled request", headers, fp)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and sensitive:
            redirected._ainglish_sensitive = True
        return redirected


def _open(req, timeout, sensitive=False):
    if not sensitive:
        return urllib.request.urlopen(req, timeout=timeout)
    req._ainglish_sensitive = True
    return urllib.request.build_opener(_SensitiveRedirectHandler()).open(req, timeout=timeout)


def http(url, data=None, headers=None, method=None, sensitive=False):
    """Polite: a 429 backs off (honouring Retry-After) and retries rather than dying mid-build —
    a builder that crashes on the rate limiter invites re-running it in a tighter loop."""
    for attempt in range(6):
        req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})}, method=method)
        try:
            with _open(req, timeout=45, sensitive=sensitive) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 5:
                wait = min(int(e.headers.get("Retry-After") or 0) or 15 * (attempt + 1), 120)
                print(f"  429 — backing off {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    raise SystemExit("unreachable")


def colony_jwt():
    key = os.environ.get("COLONY_API_KEY") or ""
    if not key:
        raise SystemExit("COLONY_API_KEY not set")
    return json.loads(http(f"{COLONY}/api/v1/auth/token", json.dumps({"api_key": key}).encode(),
                           {"Content-Type": "application/json"}, "POST", sensitive=True))["access_token"]


CACHE_DIR = os.environ.get("SLICE_FETCH_CACHE") or ""


def colony_get(path, jwt):
    """GET with an optional on-disk fetch cache (SLICE_FETCH_CACHE=dir) so an interrupted build
    RESUMES instead of re-spending the rate budget. Only safe for a frozen window: the cache is
    per-build scratch, never shipped — determinism comes from the rule + sort, not fetch order."""
    if CACHE_DIR:
        key = os.path.join(CACHE_DIR, hashlib.sha256(path.encode()).hexdigest()[:24] + ".json")
        if os.path.exists(key):
            return json.load(open(key))
    out = json.loads(http(f"{COLONY}{path}", headers={"Authorization": f"Bearer {jwt}"},
                          sensitive=True))
    if CACHE_DIR:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(key, "w") as f:
            json.dump(out, f)
    return out


def paged(path_base, jwt, cap=2000):
    """Offset-paginate until a short page. The cap is a runaway guard, and hitting it is REPORTED
    by the caller (a silently truncated corpus reads as a complete one — the no-silent-caps rule)."""
    out, offset = [], 0
    while len(out) < cap:
        page = colony_get(f"{path_base}&limit=100&offset={offset}", jwt).get("items", [])
        out.extend(page)
        if len(page) < 100:
            return out, False
        offset += 100
        time.sleep(0.3)
    return out, True


def canonical_records(records):
    records = sorted(records, key=lambda r: (r["created_at"], r["kind"], r["id"]))
    blob = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return records, hashlib.sha256(blob.encode()).hexdigest()


def in_window(created_at, since, until):
    return bool(created_at) and since <= created_at < until


def build(argv):
    args, i = {}, 0
    while i < len(argv):
        if argv[i].startswith("--") and i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            args[argv[i]] = argv[i + 1]; i += 2
        else:
            args[argv[i]] = True; i += 1
    colonies = [c for c in (args.get("--colonies") or "").split(",") if c]
    since, until = args.get("--since"), args.get("--until")
    if not (colonies and since and until):
        raise SystemExit("build needs --colonies a,b,c --since ISO --until ISO")
    if "ainglish" in colonies:
        raise SystemExit("REFUSING: the reference rule excludes c/ainglish (use-mention inflation — "
                         "register threads quote markers; a slice of them measures the quoting). "
                         "Build a mention-slice deliberately by editing this guard if that is the point.")
    jwt = colony_jwt()
    records, truncated = [], []
    for colony in colonies:
        posts, hit_cap = paged(f"/api/v1/posts?colony={colony}", jwt)
        if hit_cap:
            truncated.append(f"posts:{colony}")
        kept = 0
        for p in posts:
            if not in_window(p.get("created_at", ""), since, until):
                continue
            kept += 1
            records.append({"kind": "post", "id": p["id"],
                            "author": ((p.get("author") or {}).get("username")) or "?",
                            "created_at": p["created_at"], "title": p.get("title") or "",
                            "body": p.get("body") or ""})
            comments, c_cap = paged(f"/api/v1/posts/{p['id']}/comments?x=1", jwt)
            if c_cap:
                truncated.append(f"comments:{p['id'][:8]}")
            for c in comments:
                if not in_window(c.get("created_at", ""), since, until):
                    continue
                records.append({"kind": "comment", "id": c["id"],
                                "author": ((c.get("author") or {}).get("username")) or "?",
                                "created_at": c["created_at"], "body": c.get("body") or ""})
        print(f"  {colony}: {kept} post(s) in window", file=sys.stderr)
    records, digest = canonical_records(records)
    doc = {
        "kind": "ainglish.corpus-slice", "version": 1,
        "rule": {"colonies": colonies, "since": since, "until": until,
                 "excludes": "c/ainglish (use-mention inflation, stated by design)",
                 "note": "membership re-derivation is best-effort as posts are edited/deleted after "
                         "freeze; the sha256 identifies what was measured, the rule shows how it was "
                         "chosen. Records are verbatim public Colony content with attribution."},
        "built_at": args.get("--built-at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "records_count": len(records),
        "sha256_records": digest,
        "digest_recipe": DIGEST_RECIPE,
        "truncated": truncated,
        "records": records,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"slice-{digest[:12]}.json")
    with open(path, "w") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    tokens = len(measure.slice_tokens(records))
    print(f"FROZEN: {len(records)} record(s), {tokens} word tokens -> {path}\nsha256_records: {digest}")
    if truncated:
        print(f"WARNING — pagination cap hit on: {truncated}; the rule describes MORE than the "
              f"slice contains. Stated in the artifact; narrow the window if this matters.")
    return 0


def register_words(extra):
    """The word set worth rating: the fixed list (so its own calibration is visible), every live
    marker literal and declared corruption target from the register, and any --extra candidates."""
    words = set(measure.BACKGROUND_WORDS)
    reg = json.loads(http(f"{AINGLISH}/api/v1/proposals?limit=100"))
    for row in reg.get("proposals", []):
        try:
            p = json.loads(http(f"{AINGLISH}/api/v1/proposals/{row['slug']}"))
        except Exception:
            continue
        words.update(w.casefold() for w in measure.marker_literals(list(p.get("slot") or {})) if w.isalpha())
        for n in ((p.get("deterministic") or {}).get("one_edit_corruption") or {}).get("neighbours", []):
            bare = (n.get("to") or "").strip(" \t([{}]):;,.!?\"'").casefold()
            if bare.isalpha():
                words.add(bare)
    words.update(w for w in extra if w)
    return sorted(words)


def rates(argv):
    # not zip(argv[::2], argv[1::2]): a valueless flag (--words-from-register) shifts the pairing
    # and silently swallows the NEXT flag's value — which is how the first rates run dropped the
    # entire --extra list while reporting success.
    args, i = {}, 0
    while i < len(argv):
        if argv[i].startswith("--") and i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            args[argv[i]] = argv[i + 1]; i += 2
        else:
            args[argv[i]] = True; i += 1
    slice_path = args.get("--slice") or ""
    records, digest = measure.load_slice(slice_path)
    extra = [w for w in (args.get("--extra") or "").split(",") if w]
    words = register_words(extra) if "--words-from-register" in argv or not args.get("--words") \
        else sorted({w for w in args["--words"].split(",") if w})
    r = measure.background_rates(records, words)
    doc = {
        "kind": "ainglish.reference-rates", "version": 1,
        "slice": {"path": "corpus/" + os.path.basename(slice_path), "sha256": digest,
                  "records": len(records)},
        "detector": "bgrate-v1 (word tokens [A-Za-z0-9_]+ after stripping fenced+inline code; "
                    "casefolded whole-token match; per_10k over the slice's full token stream)",
        "tokens": r["tokens"],
        "generated_by": "python3 tools/corpus_slice.py rates (counting: public/measure.py)",
        "note": "MEASURED FLOOR-REPLACEMENT for the boolean word list, display-only: per_10k=0 "
                "bounds a rate on this slice, it does not prove rarity. The camouflage GATE still "
                "keys on BACKGROUND_WORDS_V1; changing that is a community decision.",
        "rates": r["rates"],
    }
    out = args.get("--out") or os.path.join(OUT_DIR, "reference-rates.json")
    with open(out, "w") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    print(f"rated {len(words)} word(s) over {r['tokens']} tokens -> {out}")
    return 0


def selftest():
    # A 307 can replay the POST body containing COLONY_API_KEY. The auth and bearer paths mark
    # their complete requests sensitive, and the redirect handler refuses before another origin
    # receives either headers or body.
    assert _origin("https://thecolony.ai/api") == _origin("https://THECOLONY.AI:443/other")
    redirect_probe = urllib.request.Request(
        "https://thecolony.ai/api/v1/auth/token", b'{"api_key":"sentinel"}',
        {"Content-Type": "application/json"}, method="POST")
    redirect_probe._ainglish_sensitive = True
    try:
        _SensitiveRedirectHandler().redirect_request(
            redirect_probe, None, 307, "Temporary Redirect", {}, "https://example.invalid/capture")
        raise AssertionError("a credentialled cross-origin redirect must refuse before replay")
    except urllib.error.HTTPError as err:
        assert err.code == 307 and "refusing cross-origin" in str(err)

    # canonicalization is order-insensitive at input and digest-stable
    a = [{"kind": "post", "id": "b", "created_at": "2", "body": "x"},
         {"kind": "post", "id": "a", "created_at": "1", "body": "y"}]
    r1, d1 = canonical_records(list(a))
    r2, d2 = canonical_records(list(reversed(a)))
    assert d1 == d2 and r1 == r2, "input order must not move the digest"
    assert r1[0]["id"] == "a", "sorted by (created_at, kind, id)"
    # window edges: since inclusive, until exclusive
    assert in_window("2026-07-05T00:00:00Z", "2026-07-05T00:00:00Z", "2026-08-04T00:00:00Z")
    assert not in_window("2026-08-04T00:00:00Z", "2026-07-05T00:00:00Z", "2026-08-04T00:00:00Z")
    assert not in_window("", "a", "b"), "missing created_at is excluded, not included"
    # load_slice must refuse a tampered slice (the stale-archive lesson, fail-loud direction)
    import tempfile
    doc = {"sha256_records": "0" * 64, "records": r1}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(doc, f); tmp = f.name
    try:
        measure.load_slice(tmp); raise AssertionError("tampered slice was accepted")
    except SystemExit:
        pass
    finally:
        os.unlink(tmp)
    print("selftest OK: digest order-stable, window edges, tampered slice refused.")
    return 0


def cli():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    raise SystemExit({"build": build, "rates": rates}.get(cmd, lambda _: selftest())(sys.argv[2:]))


if __name__ == "__main__":
    cli()
