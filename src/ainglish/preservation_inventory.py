"""The mint-time identity of exact-binomial-preservation-v1 (protocol row a-9mvh2ph6g1fnw0a1).

A comprehension manifest against a proposal that opted into the reading commits, before any
reader is asked::

    preservation_analysis: exact-binomial-preservation-v1
    preservation_inventory:
      kind: ainglish.preservation-inventory.v1
      readers:         [{name, lineage}, ...]
      forms:           [name, ...]
      error_endpoints: [{id, family, arm, scoring_key_sha256}, ...]
      sampling:        {unit: world, cluster_field, per_cell}

The family is fixed by that object: M = readers x forms x (2 + error endpoints). An error endpoint
is one separately elicited question on one arm with its own frozen key, so a design with two error
families lists four, and every family must be on both arms.

``check`` is the register's check (``PreservationInventory::check``) in Python, so a design can be
corrected before anything is spent. The two read one vector file,
``tests/fixtures/exact-binomial-preservation-inventory-v1.json``, whose expectations were written by
hand, and report the same set of problem codes for the same manifest. A check that depends on a part
that is itself malformed is skipped on both sides:

- the comparison with ``manifest.readers`` and the lineage count need a clean reader list;
- the comparison with ``manifest.settlement_strata`` needs a clean form list;
- the both-arms check needs clean endpoint rows;
- the feasibility of ``per_cell`` is checked when the readers, forms and error endpoints reported no
  problem at all and ``per_cell`` is an integer from 1 to the ceiling, whatever else sampling holds.

What this cannot know, and does not pretend to: that two lineage names are two base models, that the
sampled worlds are independent, that the English comparator is the shortest complete one. Those are
three independently reviewed declarations. Nothing here reads them, and a field or flag that
asserts one is refused as an unknown key. A manifest that passes this check has a complete
declaration and a cell size that is not arithmetically impossible. It has not been told that the
study is sound, and the register does not yet admit a mint against it.

Nothing in the package calls this module. It changes no request, manifest, journal or served file.
"""
import math
import re

from . import preservation as _core

KIND = "ainglish.preservation-inventory.v1"
ANALYSIS_KEY = "preservation_analysis"
MANIFEST_KEY = "preservation_inventory"
KEYS = ("kind", "readers", "forms", "error_endpoints", "sampling")
READER_KEYS = ("name", "lineage")
ENDPOINT_KEYS = ("id", "family", "arm", "scoring_key_sha256")
SAMPLING_KEYS = ("unit", "cluster_field", "per_cell")
ARMS = ("english", "ainglish")  # the arm names of the existing journal
UNIT = "world"  # version one admits only the independent-world design
MIN_LINEAGES = 2
MAX_READERS = 16  # a manifest names at most 16 models; a reader is one of them
MAX_FORMS = 64  # a form is a settlement stratum, and a manifest admits at most this many
MAX_ERROR_ENDPOINTS = 64  # bounds the family and the exact search for the smallest feasible cell

_BARE_INTEGER = re.compile(r"-?(0|[1-9][0-9]*)")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_STRATUM_ID = re.compile(r"[a-z0-9][a-z0-9._:-]{0,63}")


def _is_name(value):
    """The naming rule of the numeric core: a non-empty string that is not a bare integer."""
    return isinstance(value, str) and value != "" and not _BARE_INTEGER.fullmatch(value)


def _closed(value, keys):
    """A JSON object with exactly these keys."""
    return isinstance(value, dict) and set(value) == set(keys)


def _same_names(have, want):
    return sorted(have) == sorted(want)


def _is_list(value, most):
    return isinstance(value, list) and 0 < len(value) <= most


def _manifest_reader_names(manifest):
    raw = manifest.get("readers")
    if not isinstance(raw, list) or not raw:
        return None
    names = []
    for row in raw:
        name = row.get("name") if isinstance(row, dict) else None
        if not isinstance(name, str) or name == "":
            return None
        names.append(name)
    return names if len(set(names)) == len(names) else None


def _stratum_ids(manifest):
    """The ids of ``manifest.settlement_strata`` when it is a contract the register accepts, else None.

    The register's rule (``MeasurementStrata::contract``), which ``ainglish.panel`` also applies before
    a panel run: 1 to 64 closed ``{id, weight}`` rows, lowercase ids of 1 to 64 characters, no id
    twice, each weight a finite positive JSON number.
    """
    raw = manifest.get("settlement_strata")
    if "settlement_strata" not in manifest or not _is_list(raw, MAX_FORMS):
        return None
    ids, total = [], 0.0
    for row in raw:
        if not _closed(row, ("id", "weight")):
            return None
        ident, weight = row["id"], row["weight"]
        if not isinstance(ident, str) or not _STRATUM_ID.fullmatch(ident) or ident in ids:
            return None
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(float(weight)) or float(weight) <= 0:
            return None
        ids.append(ident)
        total += float(weight)
    return ids if math.isfinite(total) and total > 0 else None


def _readers(raw, manifest, add):
    if not _is_list(raw, MAX_READERS):
        add("readers_shape", "inventory.readers must be a list of 1 to %d {name, lineage} objects." % MAX_READERS)
        return None
    clean, names, lineages = True, [], []
    for row in raw:
        if not _closed(row, READER_KEYS):
            add("readers_shape", "Every inventory.readers row must hold exactly name and lineage. A flag beside them is not a review of the lineage.")
            clean = False
            continue
        if not _is_name(row["name"]):
            add("reader_name", "An inventory.readers name is empty, not a string, or a bare integer.")
            clean = False
        else:
            names.append(row["name"])
        if not _is_name(row["lineage"]):
            add("lineage_name", "An inventory.readers lineage is empty, not a string, or a bare integer.")
            clean = False
        else:
            lineages.append(row["lineage"])
    if len(set(names)) != len(names):
        add("reader_duplicate", "inventory.readers repeats a reader name.")
        clean = False
    if not clean:
        return None
    if len(set(lineages)) < MIN_LINEAGES:
        add("lineages_fewer_than_two", "inventory.readers must span at least two base-model lineages. Two endpoints, aliases, precision variants or fine-tunes of one base model are one lineage.")
        clean = False
    declared = _manifest_reader_names(manifest)
    if declared is None:
        add("manifest_readers", "manifest.readers must be a list of rows that each carry a unique reader name, so the inventory can be held to the readers that will be asked.")
        clean = False
    elif not _same_names(names, declared):
        add("readers_differ_from_manifest", "inventory.readers must name exactly the readers of manifest.readers: no reader may be asked that the family does not count, and none counted that is not asked.")
        clean = False
    return raw if clean else None


def _forms(raw, manifest, add):
    if not _is_list(raw, MAX_FORMS):
        add("forms_shape", "inventory.forms must be a list of 1 to %d form names." % MAX_FORMS)
        return None
    clean = True
    for form in raw:
        if not _is_name(form):
            add("form_name", "An inventory.forms name is empty, not a string, or a bare integer.")
            clean = False
    if clean and len(set(raw)) != len(raw):
        add("form_duplicate", "inventory.forms repeats a form name.")
        clean = False
    if not clean:
        return None
    strata = _stratum_ids(manifest)
    if strata is None:
        add("manifest_strata", "manifest.settlement_strata must be a valid stratum contract: under this reading each form is a settlement stratum, and the journal is grouped by it.")
        return None
    if not _same_names(raw, strata):
        add("forms_differ_from_strata", "inventory.forms must name exactly the ids of manifest.settlement_strata: a form that is not a stratum cannot be replayed, and a stratum that is not a form would be pooled away.")
        return None
    return raw


def _endpoints(raw, add):
    if not _is_list(raw, MAX_ERROR_ENDPOINTS):
        add("endpoints_shape", "inventory.error_endpoints must be a list of 1 to %d {id, family, arm, scoring_key_sha256} objects." % MAX_ERROR_ENDPOINTS)
        return None
    clean, ids, arms, pair_twice = True, [], {}, False
    for row in raw:
        if not _closed(row, ENDPOINT_KEYS):
            add("endpoints_shape", "Every inventory.error_endpoints row must hold exactly id, family, arm and scoring_key_sha256.")
            clean = False
            continue
        row_clean = True
        if not _is_name(row["id"]):
            add("endpoint_id", "An inventory.error_endpoints id is empty, not a string, or a bare integer.")
            row_clean = False
        else:
            ids.append(row["id"])
        if not _is_name(row["family"]):
            add("endpoint_family", "An inventory.error_endpoints family is empty, not a string, or a bare integer.")
            row_clean = False
        if not isinstance(row["arm"], str) or row["arm"] not in ARMS:
            add("endpoint_arm", 'An inventory.error_endpoints arm must be "english" or "ainglish".')
            row_clean = False
        if not isinstance(row["scoring_key_sha256"], str) or not _SHA256.fullmatch(row["scoring_key_sha256"]):
            add("endpoint_key", "An inventory.error_endpoints scoring_key_sha256 must be 64 lowercase hexadecimal characters: the digest of the key frozen for that endpoint.")
            row_clean = False
        if row_clean:
            on = arms.setdefault(row["family"], set())
            pair_twice = pair_twice or row["arm"] in on
            on.add(row["arm"])
        else:
            clean = False
    if len(set(ids)) != len(ids):
        add("endpoint_duplicate", "inventory.error_endpoints repeats an id.")
        clean = False
    if not clean:
        return None
    if pair_twice:
        add("endpoint_pair_duplicate", "inventory.error_endpoints lists one error family twice on the same arm. One family on one arm is one endpoint with one frozen key.")
        clean = False
    if any(len(on) != len(ARMS) for on in arms.values()):
        add("family_one_armed", "Every error family must be elicited on both arms. An inventory that names it on one arm would be counted as a smaller family than the design has.")
        clean = False
    return raw if clean else None


def _sampling(raw, add):
    """per_cell when it is an integer from 1 to the ceiling, whatever else sampling holds."""
    if not _closed(raw, SAMPLING_KEYS):
        add("sampling_shape", "inventory.sampling must hold exactly unit, cluster_field and per_cell.")
        return None
    if not isinstance(raw["unit"], str) or raw["unit"] != UNIT:
        add("sampling_unit", 'inventory.sampling.unit must be "%s": version one admits only independent sampled worlds, never case ids or repeated templates.' % UNIT)
    if not isinstance(raw["cluster_field"], str) or raw["cluster_field"] == "":
        add("sampling_cluster_field", "inventory.sampling.cluster_field must name the item field that carries the event or template cluster.")
    per_cell = raw["per_cell"]
    if type(per_cell) is not int or per_cell < 1:  # True is an int in Python and is not a count
        add("per_cell_type", "inventory.sampling.per_cell must be a JSON integer of at least 1: the fixed number of independent worlds in every reader, form and arm denominator.")
        return None
    if per_cell > _core.MAX_OBSERVATIONS:
        add("per_cell_above_ceiling", "inventory.sampling.per_cell is %d; the exact evaluation admits at most %d observations in a count." % (per_cell, _core.MAX_OBSERVATIONS))
        return None
    return per_cell


def check(manifest):
    """Check the identity a manifest carries. Every problem is reported, each with a stable code.

    Returns ``{"problems": [{"code", "message"}, ...], "inventory": dict or None, "family": dict or
    None}``. ``inventory`` is the frozen object when there is no problem at all. ``family`` holds
    ``family_size``, ``tail_fraction``, ``minimum_per_cell`` and ``per_cell`` whenever the feasibility
    check ran, also beside problems elsewhere. ``minimum_per_cell`` is a floor of arithmetic: the
    smallest cell at which a family with every answer right could support the profile. It is not a
    power calculation and not a recommended sample size.
    """
    if not isinstance(manifest, dict):
        raise TypeError("manifest must be a JSON object")
    problems = []

    def add(code, message):
        problems.append({"code": code, "message": message})

    analysis = manifest.get(ANALYSIS_KEY)
    if not isinstance(analysis, str) or analysis != _core.ANALYSIS:
        add("analysis", 'manifest.%s must be exactly "%s".' % (ANALYSIS_KEY, _core.ANALYSIS))
    if MANIFEST_KEY not in manifest:
        add("inventory_missing", "manifest.%s is missing: the readers, forms, error endpoints and cell size must be frozen in the manifest before any reader is asked." % MANIFEST_KEY)
        return {"problems": problems, "inventory": None, "family": None}
    raw = manifest[MANIFEST_KEY]
    if not isinstance(raw, dict) or not raw:
        add("inventory_shape", "manifest.%s must be an object {%s}." % (MANIFEST_KEY, ", ".join(KEYS)))
        return {"problems": problems, "inventory": None, "family": None}
    for key in raw:
        if key not in KEYS:
            add("inventory_keys", "manifest.%s.%s is not a key of the inventory. It is a closed object {%s}; a review cannot be asserted by adding a field." % (MANIFEST_KEY, key, ", ".join(KEYS)))
    for key in KEYS:
        if key not in raw:
            add("inventory_keys", "manifest.%s.%s is missing; all of {%s} are required." % (MANIFEST_KEY, key, ", ".join(KEYS)))
    if "kind" in raw and not (isinstance(raw["kind"], str) and raw["kind"] == KIND):
        add("kind", 'manifest.%s.kind must be exactly "%s".' % (MANIFEST_KEY, KIND))

    readers = _readers(raw["readers"], manifest, add) if "readers" in raw else None
    forms = _forms(raw["forms"], manifest, add) if "forms" in raw else None
    endpoints = _endpoints(raw["error_endpoints"], add) if "error_endpoints" in raw else None
    per_cell = _sampling(raw["sampling"], add) if "sampling" in raw else None

    family = None
    if readers is not None and forms is not None and endpoints is not None and per_cell is not None:
        m = len(readers) * len(forms) * (2 + len(endpoints))
        minimum = _core.minimum_per_cell(m)
        if per_cell < minimum:
            add("per_cell_infeasible", "sampling.per_cell is %d, and for this family of %d quantities (tail %s) no cell of fewer than %d observations can support the profile even when every answer is right. %d is a floor of arithmetic, not a recommended sample size."
                % (per_cell, m, _core.tail_fraction(m), minimum, minimum))
        family = {"family_size": m, "tail_fraction": _core.tail_fraction(m), "minimum_per_cell": minimum, "per_cell": per_cell}
    if problems:
        return {"problems": problems, "inventory": None, "family": family}
    return {"problems": [], "inventory": {key: raw[key] for key in KEYS}, "family": family}


def core_inventory(inventory):
    """A checked inventory in the shape ``ainglish.preservation`` counts: names only, order preserved."""
    return {"readers": [row["name"] for row in inventory["readers"]], "forms": list(inventory["forms"]),
            "error_endpoints": [row["id"] for row in inventory["error_endpoints"]]}
