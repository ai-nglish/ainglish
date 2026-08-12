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
import ipaddress
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
try:  # packaged (pip install ainglish) or a single curl-ed file — both are first-class
    from ainglish import __version__ as HARNESS_VERSION
except Exception:
    HARNESS_VERSION = "standalone"
USER_AGENT = f"ainglish-python/{HARNESS_VERSION}"
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


def _require_secure_credential_url(url, purpose):
    """Refuse cleartext credential transport, except to an explicit loopback endpoint."""
    p = urllib.parse.urlsplit(url)
    if p.scheme.lower() == "https":
        return
    host = (p.hostname or "").lower().rstrip(".")
    loopback = host == "localhost" or host.endswith(".localhost")
    if host:
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
    if p.scheme.lower() == "http" and loopback:
        return
    raise ValueError(
        f"{purpose} would send credentials to {url!r} without HTTPS; use https://, or an explicit "
        "localhost/loopback URL for local development")


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
        req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})}, method=method)
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


def colony_jwt(totp=None):
    key = os.environ.get("COLONY_API_KEY") or ""
    if not key:
        raise SystemExit("COLONY_API_KEY not set")
    try:
        from ainglish.panel import mint_colony_access_token
    except ImportError:  # adjacent single-file layout
        from panel import mint_colony_access_token
    return mint_colony_access_token(
        COLONY,
        key,
        totp=totp if totp is not None else (os.environ.get("AINGLISH_TOTP") or None),
    )


CACHE_DIR = os.environ.get("SLICE_FETCH_CACHE") or ""


def colony_get(path, jwt):
    """GET with an optional on-disk fetch cache (SLICE_FETCH_CACHE=dir) so an interrupted build
    RESUMES instead of re-spending the rate budget. Only safe for a frozen window: the cache is
    per-build scratch, never shipped — determinism comes from the rule + sort, not fetch order."""
    if CACHE_DIR:
        key = os.path.join(CACHE_DIR, hashlib.sha256(path.encode()).hexdigest()[:24] + ".json")
        if os.path.exists(key):
            return json.load(open(key))
    _require_secure_credential_url(COLONY, "Colony corpus fetch")
    out = json.loads(http(f"{COLONY}{path}", headers={"Authorization": f"Bearer {jwt}"},
                          sensitive=True))
    if CACHE_DIR:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(key, "w") as f:
            json.dump(out, f)
    return out


def paged(path_base, jwt, cap=100000):
    """Offset-paginate until a short page. The cap is a runaway guard, and hitting it is REPORTED
    by the caller (a silently truncated corpus reads as a complete one — the no-silent-caps rule)."""
    out, offset = [], 0
    while len(out) < cap:
        separator = "&" if "?" in path_base else "?"
        page = colony_get(f"{path_base}{separator}limit=100&offset={offset}", jwt).get("items", [])
        out.extend(page)
        if len(page) < 100:
            return out, False
        offset += 100
        time.sleep(0.3)
    return out, True


def collect_colony_records(colonies, since, until, jwt):
    """Return the complete stated population plus a machine-readable fetch receipt.

    Colony has no cross-post historical comment listing endpoint. Completeness therefore requires
    enumerating every visible parent posted before the window closes, then asking each parent for
    comments created since the window opened. Older threads are deliberately included: a new reply
    on an old post is prose from this window and must not disappear because of its parent's date.
    """
    records, truncated, coverage = [], [], {}
    for colony in colonies:
        post_query = urllib.parse.urlencode({"colony": colony, "until": until, "sort": "new"})
        posts, hit_cap = paged(f"/api/v1/posts?{post_query}", jwt)
        if hit_cap:
            truncated.append(f"posts:{colony}")
        kept_posts = kept_comments = 0
        for p in posts:
            if in_window(p.get("created_at", ""), since, until):
                kept_posts += 1
                records.append({"kind": "post", "id": p["id"],
                                "author": ((p.get("author") or {}).get("username")) or "?",
                                "created_at": p["created_at"], "title": p.get("title") or "",
                                "body": p.get("body") or ""})

            comment_query = urllib.parse.urlencode({"since": since, "sort": "oldest"})
            comments, c_cap = paged(f"/api/v1/posts/{p['id']}/comments?{comment_query}", jwt)
            if c_cap:
                truncated.append(f"comments:{p['id'][:8]}")
            for c in comments:
                if not in_window(c.get("created_at", ""), since, until):
                    continue
                kept_comments += 1
                records.append({"kind": "comment", "id": c["id"],
                                "author": ((c.get("author") or {}).get("username")) or "?",
                                "created_at": c["created_at"], "body": c.get("body") or ""})
        coverage[colony] = {
            "parents_fetched": len(posts),
            "posts_in_window": kept_posts,
            "comments_in_window": kept_comments,
        }
        print(f"  {colony}: {kept_posts} post(s), {kept_comments} comment(s) in window "
              f"across {len(posts)} parent(s)", file=sys.stderr)
    return records, truncated, coverage


def canonical_records(records):
    records = sorted(records, key=lambda r: (r["created_at"], r["kind"], r["id"]))
    blob = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return records, hashlib.sha256(blob.encode()).hexdigest()


def in_window(created_at, since, until):
    return bool(created_at) and since <= created_at < until


def build(argv, totp=None):
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
    jwt = colony_jwt(totp=totp)
    records, truncated, coverage = collect_colony_records(colonies, since, until, jwt)
    if truncated:
        raise SystemExit(
            "REFUSING to write an incomplete corpus slice: pagination cap hit on "
            + ", ".join(truncated)
            + ". Narrow the window or raise the explicit guard after checking the population."
        )
    records, digest = canonical_records(records)
    doc = {
        "kind": "ainglish.corpus-slice", "version": 1,
        "rule": {"colonies": colonies, "since": since, "until": until,
                 "excludes": "c/ainglish (use-mention inflation, stated by design)",
                 "comments": "all visible comments created in the window on any visible parent "
                             "post in the selected colonies, including parents older than since",
                 "note": "membership re-derivation is best-effort as posts are edited/deleted after "
                         "freeze; the sha256 identifies what was measured, the rule shows how it was "
                         "chosen. Records are verbatim public Colony content with attribution."},
        "built_at": args.get("--built-at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "records_count": len(records),
        "sha256_records": digest,
        "digest_recipe": DIGEST_RECIPE,
        "coverage": coverage,
        "truncated": truncated,
        "records": records,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"slice-{digest[:12]}.json")
    with open(path, "w") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    tokens = len(measure.slice_tokens(records))
    print(f"FROZEN: {len(records)} record(s), {tokens} word tokens -> {path}\nsha256_records: {digest}")
    return 0


LIVE_WORD_STAGES = {"proposed", "seconded", "measured", "ratified"}
AINGLISH_PAGE_SIZE = 200


def ainglish_proposals():
    """Enumerate the complete register using the reference harness's cursor walker."""
    try:
        return measure.proposal_population(AINGLISH, page_limit=AINGLISH_PAGE_SIZE, fetch=http), False
    except RuntimeError as exc:
        print("proposal population incomplete: %s" % exc, file=sys.stderr)
        return [], True


def register_word_population(extra):
    """The word set worth rating: the fixed list (so its own calibration is visible), every live
    marker literal and declared corruption target from the register, and any --extra candidates.
    Returns the words plus the coverage receipt written into the rates artifact."""
    words = set(measure.BACKGROUND_WORDS)
    rows, truncated = ainglish_proposals()
    candidates = [row for row in rows
                  if row.get("stage") in LIVE_WORD_STAGES and row.get("kind") != "protocol"]
    failures, details, eligible = [], 0, 0
    for row in candidates:
        try:
            p = json.loads(http(f"{AINGLISH}/api/v1/proposals/{row['slug']}"))
            details += 1
        except Exception as exc:
            failures.append({"slug": row.get("slug") or "?", "error": type(exc).__name__})
            continue
        deterministic = p.get("deterministic") or {}
        if deterministic.get("protocol") or deterministic.get("convention"):
            continue
        eligible += 1
        words.update(w.casefold() for w in measure.marker_literals(list(p.get("slot") or {})) if w.isalpha())
        for n in ((p.get("deterministic") or {}).get("one_edit_corruption") or {}).get("neighbours", []):
            bare = (n.get("to") or "").strip(" \t([{}]):;,.!?\"'").casefold()
            if bare.isalpha():
                words.add(bare)
    words.update(w for w in extra if w)
    coverage = {
        "fetched": len(rows),
        "candidates": len(candidates),
        "eligible": eligible,
        "details_ok": details,
        "failures": failures,
        "truncated": truncated,
        "live_stages": sorted(LIVE_WORD_STAGES),
    }
    if truncated or failures:
        print("register word coverage: " + json.dumps(coverage, sort_keys=True), file=sys.stderr)
        why = "proposal pagination was truncated" if truncated else "proposal detail fetches failed"
        raise SystemExit(f"REFUSING to write incomplete reference rates: {why}.")
    return sorted(words), coverage


def register_words(extra):
    """Backward-compatible word-only wrapper; incomplete discovery still fails loudly."""
    return register_word_population(extra)[0]


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
    coverage = None
    if "--words-from-register" in argv or not args.get("--words"):
        words, coverage = register_word_population(extra)
    else:
        words = sorted({w for w in args["--words"].split(",") if w})
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
    if coverage is not None:
        doc["register_word_coverage"] = coverage
    out = args.get("--out") or os.path.join(OUT_DIR, "reference-rates.json")
    with open(out, "w") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    print(f"rated {len(words)} word(s) over {r['tokens']} tokens -> {out}")
    return 0


def selftest():
    # A 307 can replay the POST body containing COLONY_API_KEY. The auth and bearer paths mark
    # their complete requests sensitive, and the redirect handler refuses before another origin
    # receives either headers or body.
    assert USER_AGENT == f"ainglish-python/{HARNESS_VERSION}"
    assert _origin("https://thecolony.ai/api") == _origin("https://THECOLONY.AI:443/other")
    for safe in ("https://example.test/api", "http://localhost:8920/api",
                 "http://127.0.0.1:8920/api", "http://[::1]:8920/api"):
        _require_secure_credential_url(safe, "selftest")
    for unsafe in ("http://example.test/api", "ftp://localhost/key", "relative/path"):
        try:
            _require_secure_credential_url(unsafe, "selftest")
            raise AssertionError(f"credential URL must refuse: {unsafe}")
        except ValueError:
            pass
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

    # Population completeness: a reply written in-window on a parent posted BEFORE `since` is in
    # the slice even though the old parent post itself is not. This was the production omission.
    old_paged = globals()["paged"]
    def fake_paged(path, jwt, cap=100000):
        if path.startswith("/api/v1/posts?"):
            return ([
                {"id": "old-parent", "created_at": "2026-07-01T00:00:00Z", "author": {"username": "a"}, "body": "old"},
                {"id": "new-parent", "created_at": "2026-07-06T00:00:00Z", "author": {"username": "b"}, "body": "new"},
            ], False)
        if "old-parent" in path:
            return ([{"id": "new-reply-old-thread", "created_at": "2026-07-07T00:00:00Z",
                      "author": {"username": "c"}, "body": "reply"}], False)
        return ([], False)
    globals()["paged"] = fake_paged
    try:
        got, cuts, receipt = collect_colony_records(
            ["general"], "2026-07-05T00:00:00Z", "2026-08-04T00:00:00Z", "jwt")
    finally:
        globals()["paged"] = old_paged
    assert cuts == [] and {r["id"] for r in got} == {"new-parent", "new-reply-old-thread"}
    assert receipt["general"] == {"parents_fetched": 2, "posts_in_window": 1, "comments_in_window": 1}

    # Pagination follows the server's opaque cursor beyond 200 rows. A stalled cursor must never
    # make the first page look like a complete register.
    old_http = globals()["http"]
    first = [{"slug": f"p-{n}"} for n in range(AINGLISH_PAGE_SIZE)]
    def paginating_http(url, *args, **kwargs):
        cursor = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("cursor", [None])[0]
        if cursor is None:
            return json.dumps({"proposals": first, "pagination": {
                "total": AINGLISH_PAGE_SIZE + 1, "has_more": True, "next_cursor": "opaque-next"}}).encode()
        assert cursor == "opaque-next"
        return json.dumps({"proposals": [{"slug": "last"}], "pagination": {
            "total": AINGLISH_PAGE_SIZE + 1, "has_more": False, "next_cursor": None}}).encode()
    globals()["http"] = paginating_http
    try:
        proposals, cut = ainglish_proposals()
        assert len(proposals) == AINGLISH_PAGE_SIZE + 1 and not cut
        globals()["http"] = lambda *a, **k: json.dumps({"proposals": first, "pagination": {
            "total": AINGLISH_PAGE_SIZE + 1, "has_more": True, "next_cursor": "same"}}).encode()
        proposals, cut = ainglish_proposals()
        assert proposals == [] and cut
    finally:
        globals()["http"] = old_http

    # The shared access-token helper resolves a callable TOTP at request time and marks the raw-key
    # POST sensitive, so a 2FA-enabled corpus build has the same credential path as other tools.
    try:
        from ainglish import panel as panel_module
    except ImportError:
        import panel as panel_module
    captured = {}
    class TokenResponse:
        def read(self):
            return b'{"access_token":"jwt"}'
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    old_panel_open = panel_module._open
    def token_open(req, timeout, sensitive=False):
        captured.update({"body": json.loads(req.data), "sensitive": sensitive})
        return TokenResponse()
    panel_module._open = token_open
    try:
        assert panel_module.mint_colony_access_token("https://thecolony.ai", "key", lambda: "123456") == "jwt"
    finally:
        panel_module._open = old_panel_open
    assert captured == {"body": {"api_key": "key", "totp_code": "123456"}, "sensitive": True}
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
    print("selftest OK: complete populations, TOTP auth, digest stability, and tamper refusal.")
    return 0


def cli():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    raise SystemExit({"build": build, "rates": rates}.get(cmd, lambda _: selftest())(sys.argv[2:]))


if __name__ == "__main__":
    cli()
