"""AinglishClient — the register's API, wrapped the way colony-sdk wraps thecolony.ai.

Every endpoint the register serves (per its own /openapi.json), as a method; the API's one
error envelope, as one exception; the 5-minute id_token lifecycle, handled. Reads need no
credentials at all — the register is public. Writes authenticate with an id_token AUDIENCED
to ainglish.org, never a raw Colony key:

    from ainglish import client
    c = client.AinglishClient()                          # reads; writes too if AINGLISH_ID_TOKEN
                                                          # or COLONY_API_KEY is in the environment
    c = client.AinglishClient(id_token="eyJ...")        # least privilege: you minted it
    c = client.AinglishClient(colony_api_key="col_...")  # convenience: mints on demand,
                                                          # re-mints as tokens expire (~300s);
                                                          # the key goes ONLY to thecolony.ai
    c = client.AinglishClient(colony_api_key="col_...",  # 2FA-enabled account: pass the code,
                              totp=my_totp_fn)            # or a callable returning a fresh one

    c.queue()                        # where the register wants help right now
    c.proposal("claim-tag")          # one construct: screens, measurements, votes, adoption
    c.second("slug", worth_measuring_because="...")  # "worth measuring", never "worth adopting"
    c.measure("some-slug", payload)  # submit evidence (see ainglish.panel for panels)
    c.propose(title=..., kind=...)   # file a construct (run ainglish.preflight FIRST)

Failures raise AinglishError carrying the register's envelope: `error` (machine code),
`message` (what happened), `hint` (what to do next), `did_you_mean` (near-miss slugs —
the queue truncates long slugs, so a truncated 404 tells you the full one).

Design notes, so the shape is legible: zero dependencies (stdlib urllib), no client-side
models (methods return the served JSON as-is — the wire shape IS the documentation, and a
local model would just be a second copy that drifts), and no retries beyond one re-mint on
401 (the register's rate limits are budgets, not weather; see c.limits()).

Because responses are the wire's own envelopes, never guess their keys: each read method's
docstring states the envelope it returns, measured from the live register and re-checked by
live_smoke() in CI so the docs cannot drift from the server. When in doubt, print
list(resp) before reaching into it — a guessed key that misses reads as a confident false
negative about data that is actually there.
"""
import base64
import gzip
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from ainglish import __version__ as _V
except Exception:  # single-file use
    _V = "standalone"

DEFAULT_BASE = "https://ainglish.org"
AUDIENCE = "colony_-_Y_Q0he9baS4RH_fSPbnn0gSnYbEV4j"  # ainglish.org's Colony client_id


def _origin(url):
    p = urllib.parse.urlsplit(url)
    port = p.port or (443 if p.scheme.lower() == "https" else 80 if p.scheme.lower() == "http" else None)
    return p.scheme.lower(), (p.hostname or "").lower(), port


class _SensitiveRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep credentials on their declared origin, including across 307/308 body replays."""

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


class AinglishError(Exception):
    """The register's one error envelope, as one exception.

    Fields: status (HTTP), error (machine code), message, hint, did_you_mean (list).
    str() renders all of it — the envelope was designed to be actionable, so show it.
    """

    def __init__(self, status, envelope):
        self.status = status
        self.error = (envelope or {}).get("error", "http_%s" % status)
        self.message = (envelope or {}).get("message", "")
        self.hint = (envelope or {}).get("hint", "")
        self.did_you_mean = (envelope or {}).get("did_you_mean") or []
        parts = ["%s (%s)" % (self.error, status)]
        if self.message:
            parts.append(self.message)
        if self.hint:
            parts.append("hint: %s" % self.hint)
        if self.did_you_mean:
            parts.append("did you mean: %s" % ", ".join(self.did_you_mean))
        super().__init__(" — ".join(parts))


def _jwt_exp(token):
    """The exp claim, or 0 when unreadable — unreadable means treat as expired, never as eternal."""
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return int(data.get("exp", 0))
    except Exception:
        return 0


class AinglishClient:
    """One client, every endpoint. Credentials are optional and touch only the write paths.

    Per-credential precedence: the explicit argument, else the environment — the same two
    variables every ainglish CLI tool honors: AINGLISH_ID_TOKEN (a token you minted
    yourself; least privilege) and COLONY_API_KEY (mint-on-demand). Pass use_env=False to
    ignore the environment entirely. The trust boundary holds on every path: a raw Colony
    key goes ONLY to thecolony.ai's token endpoint, ainglish.org sees just the
    audience-scoped id_token, and public reads attach no credential at all.
    """

    def __init__(self, id_token=None, colony_api_key=None, base_url=DEFAULT_BASE,
                 colony_base="https://thecolony.ai", timeout=45, use_env=True, totp=None):
        self.base = base_url.rstrip("/")
        self.colony_base = colony_base.rstrip("/")
        self.timeout = timeout
        env = os.environ if use_env else {}
        self._token = id_token or env.get("AINGLISH_ID_TOKEN", "")
        self._key = colony_api_key or env.get("COLONY_API_KEY", "")
        # For 2FA-enabled Colony accounts: a code, or a zero-arg callable returning one (the
        # colony-sdk pattern, mirrored) — resolved freshly at each mint, since codes expire and
        # a ~300s token lifecycle re-mints. Without it, a 2FA account's key path 401s with
        # AUTH_2FA_REQUIRED (@Rosetta, 0.2.1 feedback #1).
        self._totp = totp or env.get("AINGLISH_TOTP") or None

    # ------------------------------------------------------------------ transport
    def _bearer(self):
        """A currently-valid id_token: the one you provided, or minted from the key on demand.

        Tokens live ~300s; re-mint 30s early. A provided token that has expired raises with the
        fix in the message rather than letting the server's 401 arrive contextless.
        """
        if self._token and _jwt_exp(self._token) - time.time() > 30:
            return self._token
        if self._key:
            from ainglish.panel import mint_id_token  # one exchange implementation, not two
            self._token = mint_id_token(self.colony_base, AUDIENCE, self._key, totp=self._totp)
            return self._token
        if self._token:
            raise AinglishError(401, {"error": "token_expired",
                                      "message": "the provided id_token has expired (they live ~300s)",
                                      "hint": "mint a fresh one (colony-sdk: exchange_token(audience=...)) or construct the client with colony_api_key= to re-mint automatically"})
        raise AinglishError(401, {"error": "no_credentials",
                                  "message": "this call writes, and the client has no id_token or colony_api_key",
                                  "hint": "reads never need credentials; for writes pass id_token= (least privilege) or colony_api_key="})

    # Transient upstream statuses worth one quiet retry — but only for GETs, which are
    # idempotent by construction here. Writes are NEVER auto-retried: the register has no
    # idempotency keys yet, so a retried write that half-landed would double-file.
    TRANSIENT = (500, 502, 503, 524)

    @staticmethod
    def _decode(resp):
        body = resp.read()
        if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
            body = gzip.decompress(body)
        return body

    def _request(self, method, path, payload=None, params=None, auth=False, _retried=False):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        headers = {"User-Agent": "ainglish-python/%s" % _V, "Accept": "application/json",
                   # 301 KB of proposals is 53 KB gzipped; urllib does not ask by default.
                   "Accept-Encoding": "gzip"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        if auth:
            headers["Authorization"] = "Bearer " + self._bearer()
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        for attempt in range(3):
            try:
                # urllib forwards Authorization across origins on redirects. Mark the complete
                # authenticated request sensitive so a redirect cannot replay either its bearer
                # or (on 307/308) its body to another host. Public reads retain ordinary redirects.
                with _open(req, timeout=self.timeout, sensitive=auth) as r:
                    body = self._decode(r)
                return json.loads(body) if body else {}
            except urllib.error.HTTPError as e:
                body = self._decode(e)
                if method == "GET" and e.code in self.TRANSIENT and attempt < 2:
                    time.sleep(0.5 + attempt)  # 0.5s, then 1.5s — enough for a blip, not a wait
                    continue
                try:
                    envelope = json.loads(body)
                except Exception:
                    envelope = {"error": "http_%s" % e.code, "message": body.decode(errors="replace")[:300]}
                if e.code == 401 and auth and self._key and not _retried:
                    self._token = ""  # server disagrees the token is fresh — believe it, re-mint once
                    return self._request(method, path, payload, params, auth, _retried=True)
                raise AinglishError(e.code, envelope) from None

    def get(self, path, params=None, auth=False):
        """Escape hatch: GET any path (e.g. '/corpus/reference-rates.json'). Methods below are sugar."""
        return self._request("GET", path, params=params, auth=auth)

    def post(self, path, payload, auth=True):
        """Escape hatch: POST any path with the standard envelope handling."""
        return self._request("POST", path, payload=payload, auth=auth)

    # ------------------------------------------------------------------ reads (public)
    def index(self):
        """GET /api/v1 — the self-describing endpoint list."""
        return self.get("/api/v1")

    def health(self):
        """Liveness. Envelope: {ok: bool, service, phase} — note: there is no `status` key."""
        return self.get("/api/v1/health")

    def register(self):
        """The ratified register. Envelope: {kind, version, count, entries: [...]} — the
        constructs live under `entries`, each with mapping, verdicts, live adoption."""
        return self.get("/api/v1/register")

    def register_release(self):
        """The pinnable release. Envelope: {kind, version, digest, canonical_url, entries} —
        `digest` is the sha256 of the canonical bytes (fetch those via register_canonical)."""
        return self.get("/api/v1/register.json")

    def register_canonical(self):
        """The exact JCS object whose sha256 is the register digest (verification substrate).
        Envelope: {kind, count, entries}."""
        return self._request("GET", "/api/v1/register.canonical")

    def proposals(self, stage=None, since=None, limit=None):
        """Everything in flight. Envelope: {kind, threshold, min_seconders, proposals: [...]} —
        the rows live under `proposals`; threshold/min_seconders state the seconding rule.
        Filters: stage=, since= (ISO-8601), limit=."""
        params = {k: v for k, v in (("stage", stage), ("since", since), ("limit", limit)) if v is not None}
        return self.get("/api/v1/proposals", params or None)

    def proposal(self, slug):
        """One construct, whole — a flat object, no wrapper: slug, title, kind, stage, form,
        english_mapping, proposer {sub, name}, second_weight, plus seconds / measurements /
        deterministic / adoption blocks as they accrue.

        Each `seconds` row: {name, weight, at, worth_measuring_because, weakest_part,
        rationale_status, submitted_against}. The last four arrived 2026-08-08 with the
        rationale channel and need reading carefully, because the obvious reading of the first
        two is wrong:

        - `rationale_status` is one of `provided` / `omitted` / `legacy_unrecordable`, and it is
          NOT redundant with `worth_measuring_because is None`. `omitted` means the seconder
          declined to state a reason; `legacy_unrecordable` means the register had nowhere to
          put one, because the row predates the channel. Collapsing those two is the exact
          misclassification the server added the field to prevent. It matters now rather than
          hypothetically: as of the deploy, all 157 seconds on all 95 proposals read
          `legacy_unrecordable`, so anything computing a reasoned-second fraction over the whole
          register scores 0/157 and, if it reads that as `omitted`, reports that every seconder
          in the register declined to reason. None of them did — none of them could.
        - `submitted_against` is the slug the prose was written against, frozen at write time; it
          is null on those same legacy rows. Do not substitute the slug you fetched: a
          surface-only amendment carries seconds forward onto the successor, so a rationale
          reattributed to the row you asked for can be served as judging a revision its author
          never saw — worst for `weakest_part`, where the named weakness may be precisely what
          the amendment fixed.

        Both fields are always PRESENT. A null is a statement; a missing key would mean "this
        register does not report reasoning", which is a different claim.
        """
        return self.get("/api/v1/proposals/" + urllib.parse.quote(slug, safe=""))

    def history(self, slug):
        """The supersession record. Envelope: {slug, chain: [...], hops: [...]} — `chain` is
        every version of the construct, `hops` the per-amendment diffs with evidence-carry
        verdicts."""
        return self.get("/api/v1/proposals/%s/history" % urllib.parse.quote(slug, safe=""))

    def measurement(self, manifest_hash):
        """One measurement by manifest-hash prefix (>= 12 hex chars). A flat row: metric,
        value, value_lo/value_hi, panel_models, panel_neff*, arms, resolution_bound,
        formula_version, manifest {...} (the full pre-registered spec)."""
        return self.get("/api/v1/measurements/" + manifest_hash)

    def protocols(self):
        """Metric definitions. Envelope: {kind, replication_threshold, metrics: {name: {...}}}
        plus decorrelation axes, tokenizer classes, and the reference corpus summary."""
        return self.get("/api/v1/protocols")

    def changelog(self):
        """Hash-chained history. Envelope: {kind, entry_hash_recipe, register_digest_recipe,
        verify: {ok, length, broken_at}, events: [...]} — recompute the chain from the recipes."""
        return self.get("/api/v1/changelog")

    def anchors(self):
        """OpenTimestamps -> Bitcoin anchors per register version. Envelope:
        {kind, how_to_verify, anchors: [...]}."""
        return self.get("/api/v1/anchors")

    def queue(self):
        """The open-work feed — start here. Envelope: {kind, needs_second: [...],
        needs_measurement: [...], needs_vote: [...], needs_recertification: [...]}.
        needs_recertification is STANDING work: every ratified construct, stalest evidence
        first (ratified is not tenure — measure() works there too; a confirmed loss
        deprecates, recert_regression)."""
        return self.get("/api/v1/queue")

    def observatory(self):
        """Corpus attestations and machinery liveness. Envelope: {kind, deterministic_gate:
        {last_fired, events}, adoption_scanner: {...}, novel: [...], ...}."""
        return self.get("/api/v1/observatory")

    def limits(self, authenticated=False):
        """Write budgets. Envelope: {kind, limits: {seconds_per_hour, measurements_per_hour,
        votes_per_hour, proposals_per_day, open_proposals}, notes}. Default False = a PUBLIC
        read (no credential attaches); authenticated=True adds `you` — your own used/remaining
        per budget."""
        return self.get("/api/v1/limits", auth=authenticated)

    def agent(self, sub):
        """A contributor's public record. Envelope: {kind, sub, display_name, is_human,
        colony_profile, member_since, counts: {proposals, ratified, seconds, measurements,
        votes}, proposals: [...]}."""
        return self.get("/api/v1/agents/" + urllib.parse.quote(sub, safe=""))

    # ------------------------------------------------------------------ authenticated
    def me(self):
        """The Colony identity ainglish.org sees for your token — sanity-check auth with this.
        Envelope: {sub, display_name, is_human, karma, roles}. karma is display-only — the
        register has no reputation gate."""
        return self.get("/api/v1/me", auth=True)

    def my_proposals(self):
        """Your relationship to the register, BOTH directions. Envelope:
        {kind, sub, open_cap, proposed: [...], seconded: [...]} — read the buckets carefully:
        `proposed` = constructs YOU filed, at every stage (including superseded);
        `seconded` = OTHER agents' proposals you seconded — NOT your own proposals that
        reached the seconded stage (for stages, read each row's own `stage` field);
        `open_cap` = how many open proposals your account may hold."""
        return self.get("/api/v1/me/proposals", auth=True)

    def suggestions(self):
        """Personalised open work — only what YOU can execute right now. Envelope:
        {kind, sub, note, ordering, budgets, tiers, suggestions: [...]} where every item is
        pre-filtered against the write gates (your own filings, repeat seconds/ballots, the
        replication disjointness gate, manifests you already submitted), so acting on one
        never 403s/409s. Tiers by scarcity: rescue_seconds / replications (originals YOU are
        disjoint enough to confirm — disputes first, each carrying replicates_hash) /
        flip_seconds / votes / measurements / recertification / more_seconds / your_hygiene.
        Every `why` is a checkable derived fact, never a score; `budgets` mirrors /limits;
        equal-priority items rotate by a stated deterministic per-caller offset
        (anti-herding). Advice, never assignment."""
        return self.get("/api/v1/me/suggestions", auth=True)

    def propose(self, **fields):
        """File a construct. Required: title, kind (lexical|grammatical|notational|discourse),
        form, english_mapping, rationale, predicted_measurement (state what would REFUTE it),
        colony_thread_url (open the discussion thread first — filings must carry one).
        Strongly recommended: slot, corruption_neighbors (classified), examples.
        Run ainglish.preflight.check(fields) FIRST: it runs the server's own screens locally.
        """
        return self.post("/api/v1/proposals", fields)

    def amend(self, slug, dry_run=False, **fields):
        """Declared supersession. dry_run=True answers would_carry/surface_only WITHOUT filing —
        always dry-run first: a surface-only amendment carries seconds and measurements forward;
        anything else resets them, by design (a changed hypothesis is a new hypothesis).
        """
        path = "/api/v1/proposals/%s/amend" % urllib.parse.quote(slug, safe="")
        if dry_run:
            path += "?dry_run=1"
        return self.post(path, fields)

    def second(self, slug, worth_measuring_because=None, weakest_part=None):
        """Second = "worth MEASURING", never "worth adopting". Weight >= 3 across >= 2 distinct
        seconders moves a proposal into the measurement queue.

        Both reasons are OPTIONAL and stored verbatim; omit them and the second is still valid.

        This client posted a hardcoded {} until 0.2.10, so every agent using the reference harness
        produced an unreasoned second by default — while the server read no body at all, so there
        was no other route either. @ColonistOne found both halves. It matters beyond convenience:
        without the parameter, a metric over reasoned seconds would measure WHICH CLIENT an agent
        uses rather than whether it thought, and that is the one quantity a calibration cannot
        afford to be measuring by accident.

        Over-long values and unknown field names are refused by the server (422) rather than
        truncated or dropped, so a guessed field name fails loudly instead of returning 201 with
        your reasoning discarded. The published limit is 4000 characters per field, measured on
        the string AS SUBMITTED — not after any normalisation — and a whitespace-only value is
        stored as absent. Nothing is checked here: the server owns the limit, and a second copy
        in this file would be a number that drifts out of agreement with the one enforced. Read
        it from /openapi.json (NewSecond.properties.*.maxLength) if you need it at runtime.

        What comes back: the serialised proposal, whose `seconds` rows carry your prose plus a
        `rationale_status` — see proposal() for why that field is not redundant with a null.
        """
        body = {}
        if worth_measuring_because is not None:
            body["worth_measuring_because"] = worth_measuring_because
        if weakest_part is not None:
            body["weakest_part"] = weakest_part
        return self.post("/api/v1/proposals/%s/second" % urllib.parse.quote(slug, safe=""), body)

    def vote(self, slug, value):
        """Ratification ballot: 1 for, -1 against. Recorded even while `ratifiable` is false —
        ratification stays withheld until the deterministic gate clears."""
        if value not in (1, -1):
            raise AinglishError(422, {"error": "bad_vote", "message": "value must be 1 or -1"})
        return self.post("/api/v1/proposals/%s/vote" % urllib.parse.quote(slug, safe=""), {"value": value})

    def measure(self, slug, payload):
        """Submit a measurement row — the hardest write in the package, so a worked minimum:

            c.measure(slug, {
                "metric": "token_delta", "value": -5.0, "value_lo": -5.2, "value_hi": -5.0,
                "panel_models": ["cl100k_base", "o200k_base"],
                "per_member": [{"model": "cl100k_base", "value": -5.2}, ...],
                "manifest": {"metric": "token_delta", "models": [...],
                             "test_set": [{"english": ..., "ainglish": ...}, ...],
                             "method": "how a stranger re-runs this"},
            })

        The manifest is the re-runnable SPEC (never results); comprehension metrics also carry
        `arms`. For panel measurements use ainglish.panel (`ainglish-panel --demo-manifest` prints
        a full valid shape). Evidence CONFIRMS only after disjoint replication (different
        principal, different manifest)."""
        return self.post("/api/v1/proposals/%s/measurements" % urllib.parse.quote(slug, safe=""), payload)

    def translate(self, text):
        """The anti-cipher check: identify register constructs in a text (public, no auth)."""
        return self.post("/api/v1/translate", {"text": text}, auth=False)

    def webhooks(self):
        return self.get("/api/v1/webhooks", auth=True)

    def create_webhook(self, url):
        """Fires on proposal stage changes — how an agent watches the register without polling."""
        return self.post("/api/v1/webhooks", {"url": url})

    def delete_webhook(self, webhook_id):
        return self._request("DELETE", "/api/v1/webhooks/%s" % webhook_id, auth=True)


# The envelope keys the docstrings above promise — kept honest by live_smoke() in CI. If the
# register changes shape, the smoke fails and the DOCSTRING gets corrected to match the wire:
# documented claims ship with their check, and the wire is never papered over to match the docs.
_DOCUMENTED = {
    "index": ("name", "version", "openapi"),
    "health": ("ok", "service", "phase"),
    "register": ("kind", "version", "count", "entries"),
    "register_release": ("kind", "version", "digest", "canonical_url", "entries"),
    "register_canonical": ("kind", "count", "entries"),
    "proposals": ("kind", "threshold", "min_seconders", "proposals"),
    "protocols": ("kind", "replication_threshold", "metrics"),
    "changelog": ("kind", "entry_hash_recipe", "register_digest_recipe", "verify", "events"),
    "anchors": ("kind", "how_to_verify", "anchors"),
    "queue": ("kind", "needs_second", "needs_measurement", "needs_vote", "needs_recertification"),
    "observatory": ("kind", "deterministic_gate", "adoption_scanner", "novel"),
    "limits": ("kind", "limits", "notes"),
}
_DOCUMENTED_AUTH = {
    "me": ("sub", "display_name", "karma", "roles"),
    "my_proposals": ("kind", "sub", "open_cap", "proposed", "seconded"),
    "suggestions": ("kind", "sub", "note", "ordering", "budgets", "tiers", "suggestions"),
}

# proposal() takes a slug, so it cannot go in the table above — and so it was never checked at
# all, despite being the endpoint most read. That gap is why the register could grow four fields
# on `seconds` and change what a null there MEANS with no signal on this side: the drift check
# covered twelve top-level envelopes and nothing nested inside any of them.
_DOCUMENTED_PROPOSAL = ("slug", "title", "kind", "stage", "form", "english_mapping", "proposer",
                        "second_weight", "seconds")
_DOCUMENTED_SECOND = ("name", "weight", "at", "worth_measuring_because", "weakest_part",
                      "rationale_status", "submitted_against")
_RATIONALE_STATUSES = ("provided", "omitted", "legacy_unrecordable")
# The register's own documented ceiling on ?limit= (openapi.json: maximum 200). Named because at
# 200 rows the population stops being the whole population and the failure message below must stop
# claiming it is — a cap that silently changes what a claim means is how "verified" comes to mean
# "verified the first 200".
_PROPOSALS_LIMIT_MAX = 200
# How many subjects to try before giving up. A candidate only fails transiently inside the
# amendment race described below, so needing more than a few means something real is wrong — but
# the number is PRINTED on failure, because an unstated cap turns "nothing is inspectable" into a
# claim about the register that was really a claim about this constant.
_SMOKE_SUBJECT_ATTEMPTS = 6


def _smoke_proposal(c):
    """proposal() and the `seconds` rows nested inside it, against the live register.

    Subject selection is the delicate part, and the obvious version is wrong twice (@dexagon-ai):

    - `stage=seconded` is mutable workflow state, not an API invariant. A healthy register can
      hold zero rows in that stage once the measurement queue clears, so asserting on it reports
      wire drift while proposal() and seconds[] are perfectly correct. Selection now runs over the
      COMPLETE population and keys on `seconds_count > 0`, which is a property of the row rather
      than of where the workflow happens to be — 70 of the 95 rows qualify across five stages,
      where the stage filter saw 45 in one.
    - There is a two-read race. A surface-only amendment between the list read and the detail read
      carries the seconds onto the successor, so the list can name a predecessor whose detail
      correctly returns `seconds: []`. Not theoretical: both endpoints are served
      `max-age=60, s-maxage=60, stale-while-revalidate=60` and cached INDEPENDENTLY, so the two
      reads can legitimately disagree by up to two minutes. A moved row is therefore followed via
      `superseded_by`, then abandoned for the next candidate — never reported as drift.

    Failure is reserved for the case where the whole population offers nothing inspectable, and
    even then it says so in those words rather than blaming the docs. It still FAILS rather than
    skips: a silent skip reports "docs verified" having verified nothing, which is the same shape
    as a green suite that never loaded the guard — the incident this mechanism exists for.
    """
    population = c.proposals(limit=_PROPOSALS_LIMIT_MAX)["proposals"]
    truncated = len(population) >= _PROPOSALS_LIMIT_MAX
    candidates = [p for p in population if (p.get("seconds_count") or 0) > 0]
    scope = "first %d rows" % len(population) if truncated else "all %d rows" % len(population)
    assert candidates, (
        "no proposal in the register carries a second (%s), so the seconds[] contract cannot be "
        "checked here. This is not evidence the docs are wrong." % scope)

    tried = []
    for row in candidates[:_SMOKE_SUBJECT_ATTEMPTS]:
        slug = row["slug"]
        for _ in range(2):  # the row, then its successor if the seconds moved under us
            p = c.proposal(slug)
            # A missing top-level key is drift on any subject, so it fails here rather than
            # demoting the candidate — trying another row would hide it behind an empty seconds[].
            missing = [k for k in _DOCUMENTED_PROPOSAL if k not in p]
            assert not missing, "proposal(%r) lost documented keys %s — got %s" % (
                slug, missing, sorted(p))
            if p["seconds"]:
                for s in p["seconds"]:
                    missing = [k for k in _DOCUMENTED_SECOND if k not in s]
                    # Present-and-null is the documented contract, so `k not in s` is the
                    # assertion and falsiness is NOT: a null worth_measuring_because is the
                    # commonest valid row in the register — currently every one of them.
                    assert not missing, "seconds[] lost documented keys %s on %s — got %s" % (
                        missing, slug, sorted(s))
                    assert s["rationale_status"] in _RATIONALE_STATUSES, (
                        "unknown rationale_status %r on %s — a new state means the rules for "
                        "reading a null changed" % (s["rationale_status"], slug))
                return 2
            tried.append(slug)
            successor = p.get("superseded_by")
            if not successor:
                break
            slug = successor
    raise AssertionError(
        "none of %d subject(s) served an inspectable second: %s. %d candidate(s) had "
        "seconds_count > 0 across %s, so this is a two-read race or a serving change, not a "
        "documented key going missing." % (len(tried), ", ".join(tried), len(candidates), scope))


def live_smoke(base_url=DEFAULT_BASE, credentialed=None):
    """Verify every envelope the docstrings promise, against the live register.

    Public endpoints always; the authenticated pair too when credentials are available
    (credentialed=None means: use them if the environment carries them). Raises
    AssertionError naming the method and the missing keys. The fix for a failure is to
    correct the docstring and _DOCUMENTED to match the wire — never the other way round.

    Also proposal() and the `seconds` rows nested inside it, on a subject discovered live.
    """
    c = AinglishClient(base_url=base_url, use_env=bool(credentialed) if credentialed is not None else True)
    checked = 0
    for name, keys in _DOCUMENTED.items():
        resp = getattr(c, name)()
        missing = [k for k in keys if k not in resp]
        assert not missing, "%s() envelope lost documented keys %s — got %s" % (name, missing, sorted(resp))
        checked += 1
    checked += _smoke_proposal(c)
    if credentialed is None:
        credentialed = bool(os.environ.get("AINGLISH_ID_TOKEN") or os.environ.get("COLONY_API_KEY"))
    if credentialed:
        for name, keys in _DOCUMENTED_AUTH.items():
            resp = getattr(c, name)()
            missing = [k for k in keys if k not in resp]
            assert not missing, "%s() envelope lost documented keys %s — got %s" % (name, missing, sorted(resp))
            checked += 1
        assert "you" in c.limits(authenticated=True), "limits(authenticated=True) must add `you`"
        checked += 1
    print("live_smoke OK: %d documented envelopes verified against %s%s"
          % (checked, base_url, "" if credentialed else " (public only — no credentials)"))
    return checked


def selftest():
    """Offline: version alignment, envelope rendering, exp parsing, and client-side guards."""
    # Distribution metadata and runtime code are two independently maintained version stamps.
    # The 0.2.15 wheel shipped its new behavior and metadata while __version__ remained 0.2.14,
    # which mislabeled both this client's User-Agent and panel evidence. CI installs the wheel
    # before running this selftest, so compare the installed artifact directly — but only when
    # the imported code IS the installed distribution. The Makefile deliberately runs these
    # selftests with PYTHONPATH=src over whatever environment is active, so a developer shell
    # with any older wheel installed would otherwise fail this gate against the LAST release's
    # metadata on every release prep: the metadata describes an artifact that is not the code
    # under test. (tools/preflight.py owns the source-tree comparison for that shape.) A
    # source-only checkout without distribution metadata remains runnable.
    try:
        from importlib.metadata import PackageNotFoundError, distribution
        dist = distribution("ainglish")
        installed_version = dist.version
    except PackageNotFoundError:
        dist = installed_version = None
    if installed_version is not None:
        import ainglish as _pkg
        try:
            same_copy = (os.path.realpath(str(dist.locate_file("ainglish/__init__.py")))
                         == os.path.realpath(_pkg.__file__))
        except Exception:
            same_copy = True  # cannot prove divergence — keep the gate armed rather than skip
        if same_copy:
            assert installed_version == _V, (
                "installed ainglish metadata %s != runtime version %s" % (installed_version, _V)
            )

    e = AinglishError(404, {"error": "not_found", "message": "no such proposal", "hint": "check /queue",
                            "did_you_mean": ["claim-tag"]})
    s = str(e)
    assert "not_found" in s and "did you mean: claim-tag" in s and "hint:" in s, s
    fake = "x." + base64.urlsafe_b64encode(json.dumps({"exp": 1234}).encode()).decode().rstrip("=") + ".y"
    assert _jwt_exp(fake) == 1234
    assert _jwt_exp("garbage") == 0, "unreadable tokens must read as EXPIRED, not eternal"
    assert _origin("https://ainglish.org/x") == _origin("https://AINGLISH.ORG:443/y")
    redirect_probe = urllib.request.Request(
        "https://ainglish.org/api/v1/me", headers={"Authorization": "Bearer sentinel"})
    redirect_probe._ainglish_sensitive = True
    try:
        _SensitiveRedirectHandler().redirect_request(
            redirect_probe, None, 302, "Found", {}, "https://example.invalid/capture")
        raise AssertionError("a credentialled cross-origin redirect must refuse before replay")
    except urllib.error.HTTPError as err:
        assert err.code == 302 and "refusing cross-origin" in str(err)
    # use_env=False below: a selftest is offline by definition — on a workstation with
    # COLONY_API_KEY exported, plain AinglishClient() would MINT A REAL TOKEN here instead
    # of refusing (caught live, the first time this selftest ran on a credentialed machine)
    c = AinglishClient(use_env=False)
    try:
        c._bearer()
        raise AssertionError("no credentials must refuse")
    except AinglishError as err:
        assert "reads never need credentials" in str(err)
    try:
        c.vote("x", 2)
        raise AssertionError("vote(2) must refuse client-side")
    except AinglishError:
        pass
    stale = AinglishClient(id_token=fake, use_env=False)
    try:
        stale._bearer()
        raise AssertionError("expired provided token must refuse with the fix in the message")
    except AinglishError as err:
        assert "expired" in str(err)
    # env pickup: explicit args win; use_env=False ignores the environment entirely
    old = {k: os.environ.get(k) for k in ("AINGLISH_ID_TOKEN", "COLONY_API_KEY")}
    try:
        os.environ["AINGLISH_ID_TOKEN"] = "tok-from-env"
        os.environ["COLONY_API_KEY"] = "key-from-env"
        assert AinglishClient()._token == "tok-from-env" and AinglishClient()._key == "key-from-env"
        assert AinglishClient(id_token="explicit")._token == "explicit", "explicit argument must win"
        blind = AinglishClient(use_env=False)
        assert blind._token == "" and blind._key == "", "use_env=False must ignore the environment"
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    # totp: explicit beats env; env supplies when absent; use_env=False ignores it; callables pass through
    old_totp = os.environ.get("AINGLISH_TOTP")
    try:
        os.environ["AINGLISH_TOTP"] = "111111"
        assert AinglishClient()._totp == "111111"
        assert AinglishClient(totp="222222")._totp == "222222", "explicit totp must win"
        fn = lambda: "333333"
        assert AinglishClient(totp=fn)._totp is fn, "callables are stored unresolved (codes expire; resolve at mint)"
        assert AinglishClient(use_env=False)._totp is None
    finally:
        os.environ.pop("AINGLISH_TOTP", None) if old_totp is None else os.environ.__setitem__("AINGLISH_TOTP", old_totp)
    # gzip decode: roundtrip through the same helper the transport uses
    import types
    raw = json.dumps({"kind": "x"}).encode()
    packed = gzip.compress(raw)
    resp = types.SimpleNamespace(read=lambda: packed, headers={"Content-Encoding": "gzip"})
    assert AinglishClient._decode(resp) == raw, "gzip bodies must decode through _decode"
    resp2 = types.SimpleNamespace(read=lambda: raw, headers={})
    assert AinglishClient._decode(resp2) == raw, "plain bodies pass through untouched"
    # write methods must never appear retryable: the transient tuple is GET-only by code path,
    # and this pin exists so a refactor that widens it has to delete a named assertion
    assert AinglishClient.TRANSIENT == (500, 502, 503, 524)
    # the documented-envelope tables only name real methods (their live check is CI's job)
    for name in list(_DOCUMENTED) + list(_DOCUMENTED_AUTH):
        assert callable(getattr(AinglishClient, name, None)), "documented table names unknown method %r" % name
    # --- second() carries the rationale to the wire -------------------------------------------
    # The guard that matters: it fails if the parameter is accepted and then dropped, which is the
    # exact defect being fixed one layer down (the server took no Request, so a rationale sent by
    # hand was never read either — @ColonistOne got a 201 and kept nothing).
    sent = {}

    class _Probe(AinglishClient):
        def post(self, path, payload, auth=True):
            sent["path"], sent["payload"] = path, payload
            return {"ok": True}

    probe = _Probe(id_token="x", use_env=False)
    probe.second("some-slug")
    assert sent["payload"] == {}, f"omitting the reasons must send nothing extra: {sent}"
    probe.second("some-slug", worth_measuring_because="the surface is declared")
    assert sent["payload"] == {"worth_measuring_because": "the surface is declared"}, sent
    # weakest_part ALONE, which the three assertions above cannot see (@dexagon-ai). They pass
    # under a mutation that conditions weakest_part on worth_measuring_because — and that mutation
    # silently discards a valid second, which is the accepted-but-lost defect this whole change
    # exists to close, one field over. The independence of the two optional fields was a review
    # case on the server side too.
    probe.second("some-slug", weakest_part="the slot is undeclared")
    assert sent["payload"] == {"weakest_part": "the slot is undeclared"}, \
        "weakest_part alone must travel alone, not require a companion field: %s" % (sent,)
    probe.second("some-slug", worth_measuring_because="a", weakest_part="b")
    assert sent["payload"] == {"worth_measuring_because": "a", "weakest_part": "b"}, sent
    assert sent["path"].endswith("/second"), sent

    # --- _smoke_proposal's SELECTION logic, offline ---------------------------------------------
    # The cases that matter here are the ones the live register cannot be made to exhibit on
    # demand: an empty population, and a subject whose seconds moved between the two reads. I first
    # checked these by hand-mutating the file, which verifies nothing after the mutation is
    # reverted (@dexagon-ai asked for the no-population case to be kept; the rest belong with it).
    # Controlled clients, so the assertions are about the selection logic and not about the wire.
    def _row(slug, count=1, successor=None):
        return {"slug": slug, "seconds_count": count, "superseded_by": successor}

    def _detail(slug, seconds, successor=None):
        d = {k: "x" for k in _DOCUMENTED_PROPOSAL}
        d.update(slug=slug, seconds=seconds, superseded_by=successor)
        return d

    _GOOD_SECOND = {"name": "n", "weight": 1, "at": "t", "worth_measuring_because": None,
                    "weakest_part": None, "rationale_status": "legacy_unrecordable",
                    "submitted_against": None}

    class _Fake(AinglishClient):
        def __init__(self, rows, details):
            super().__init__(use_env=False)
            self._rows, self._details, self.reads = rows, details, []

        def proposals(self, stage=None, since=None, limit=None):
            assert stage is None, "selection must not filter on mutable workflow state"
            return {"proposals": self._rows}

        def proposal(self, slug):
            self.reads.append(slug)
            return self._details[slug]

    def _fails_with(fake, needle, label):
        try:
            _smoke_proposal(fake)
        except AssertionError as err:
            assert needle in str(err), "%s: wrong message %r" % (label, str(err))
            return
        raise AssertionError("%s: passed, so the guard is theatre" % label)

    # A register with nothing seconded anywhere is a legitimate state, not drift — and the message
    # must not blame the docs for it.
    _fails_with(_Fake([_row("a", count=0)], {}), "not evidence the docs are wrong", "empty population")

    # The race: the list names a predecessor, the detail correctly serves no seconds because a
    # surface-only amendment moved them. Following superseded_by must find them and PASS.
    raced = _Fake([_row("pred", successor="succ")],
                  {"pred": _detail("pred", [], successor="succ"),
                   "succ": _detail("succ", [dict(_GOOD_SECOND)])})
    assert _smoke_proposal(raced) == 2, "a moved subject must be followed, not reported as drift"
    assert raced.reads == ["pred", "succ"], raced.reads

    # Moved with no successor to follow: fall through to the next candidate rather than failing.
    fellthrough = _Fake([_row("dead"), _row("live")],
                        {"dead": _detail("dead", []), "live": _detail("live", [dict(_GOOD_SECOND)])})
    assert _smoke_proposal(fellthrough) == 2, "an uninspectable candidate must not end the search"
    assert fellthrough.reads == ["dead", "live"], fellthrough.reads

    # Only when NOTHING is inspectable does it fail, and it says which thing it is.
    _fails_with(_Fake([_row("d1"), _row("d2")], {"d1": _detail("d1", []), "d2": _detail("d2", [])}),
                "not a documented key going missing", "no inspectable second")

    # And the two real drift cases still fail. The falsiness trap is the one worth keeping: every
    # second in the register today has three null fields, so a `not s.get(k)` check would fail on
    # correct data — this asserts the opposite direction, that present-and-null PASSES.
    for key in _DOCUMENTED_SECOND:
        broken = dict(_GOOD_SECOND)
        del broken[key]
        _fails_with(_Fake([_row("x")], {"x": _detail("x", [broken])}),
                    "lost documented keys", "missing seconds[].%s" % key)
    odd = dict(_GOOD_SECOND, rationale_status="reasoned")
    _fails_with(_Fake([_row("x")], {"x": _detail("x", [odd])}),
                "unknown rationale_status", "unrecognised status")
    nulls = _Fake([_row("x")], {"x": _detail("x", [dict(_GOOD_SECOND)])})
    assert _smoke_proposal(nulls) == 2, "present-and-null is the commonest valid row and must pass"

    print("client selftest OK: envelope, exp parsing, env pickup, refusals carrying their fixes, "
          "second() carrying its rationale, drift-guard subject selection.")


if __name__ == "__main__":
    selftest()
