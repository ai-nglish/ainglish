"""Offline surface diagnostics: no tokenizer, inference, network or semantic verdict."""

from collections import Counter
import copy
import re


def audit(pairs, declaration=None):
    """Inspect explicit surface requirements without changing the submitted pairs.

    ``forms`` declares literal marked-arm spellings. ``followed_by`` is either
    ``boundary`` (default) or ``whitespace`` for a bare form followed by an argument.
    References and protected terms are exact, case-sensitive strings counted in
    both arms. These declarations are review instructions, not register metadata.
    """
    declaration = {} if declaration is None else declaration
    if not isinstance(declaration, dict) or set(declaration) - {"forms", "references", "protected_terms"}:
        raise ValueError("input_review allows only forms, references and protected_terms")
    forms = declaration.get("forms", [])
    if not isinstance(forms, list):
        raise ValueError("input_review.forms must be a list")
    for form in forms:
        if not isinstance(form, dict) or set(form) - {"literal", "followed_by"} or \
                not isinstance(form.get("literal"), str) or not form["literal"].strip() or \
                form.get("followed_by", "boundary") not in ("boundary", "whitespace"):
            raise ValueError("Each form needs a nonempty literal and boundary or whitespace continuation")
    for field in ("references", "protected_terms"):
        values = declaration.get(field, [])
        if not isinstance(values, list) or any(not isinstance(s, str) or not s.strip() for s in values):
            raise ValueError("input_review.%s must contain nonempty literal strings" % field)
        if len(set(values)) != len(values):
            raise ValueError("Duplicate input_review.%s literal" % field)
    if not isinstance(pairs, list) or not pairs or len(pairs) > 512:
        raise ValueError("Surface review requires 1 to 512 complete pairs")

    def occurrences(text, literal, continuation="boundary"):
        # Escape user literals: there is deliberately no executable regex language.
        left = r"(?<![\w-])" if re.match(r"\w", literal[0]) else ""
        right = r"(?=\s)" if continuation == "whitespace" else (
            r"(?![\w-])" if re.match(r"\w", literal[-1]) else "")
        return len(re.findall(left + re.escape(literal) + right, text))

    rows, warnings = [], []
    for index, pair in enumerate(pairs, 1):
        if isinstance(pair, dict):
            english, marked = pair.get("english", pair.get("baseline")), pair.get("ainglish")
            if "english" in pair and "baseline" in pair and pair["english"] != pair["baseline"]:
                raise ValueError("Conflicting English arms at pair %d" % index)
        elif isinstance(pair, list) and len(pair) == 2:
            english, marked = pair
        else:
            raise ValueError("Invalid complete pair %d" % index)
        if any(not isinstance(s, str) or not s.strip() for s in (english, marked)):
            raise ValueError("Nonempty string arms required at pair %d" % index)
        matches = [{"literal": f["literal"], "followed_by": f.get("followed_by", "boundary"),
                    "count": occurrences(marked, f["literal"], f.get("followed_by", "boundary"))}
                   for f in forms]
        if forms and not any(f["count"] for f in matches):
            warnings.append({"code": "no_declared_form_match", "pair": index})
        checks = {}
        for field in ("references", "protected_terms"):
            checks[field] = []
            for literal in declaration.get(field, []):
                counts = [occurrences(s, literal) for s in (english, marked)]
                checks[field].append({"literal": literal, "english": counts[0], "ainglish": counts[1]})
                if counts[0] != counts[1]:
                    warnings.append({"code": "unequal_" + field, "pair": index, "literal": literal})
        # Word inventories expose potential omissions; paraphrases naturally differ.
        words = [Counter(re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", s.casefold())) for s in (english, marked)]
        differences = [sorted((words[i] - words[1-i]).items()) for i in (0, 1)]
        rows.append({"pair": index, "form_matches": matches, **checks,
                     "english_only_words": differences[0][:40], "ainglish_only_words": differences[1][:40],
                     "word_lists_truncated": any(len(x) > 40 for x in differences)})
    return {"kind": "ainglish.pair-content-audit.v1", "report_only": True,
            "semantic_equivalence": "not_checked", "eligibility": "not_assessed",
            "reader_calls": 0, "tokenizer_calls": 0, "api_calls": 0,
            "contains_source_fragments": True, "declaration": copy.deepcopy(declaration),
            "form_check": "declared_literals_only" if forms else "not_declared",
            "pairs": rows, "warnings": warnings,
            "interpretation": "Literal, case-sensitive form/reference/term counts and case-folded word inventories only. "
            "No form parsing, reference resolution, entailment, fairness or independent eligibility certification. "
            "Missing words may be legitimate paraphrases; equal words may conceal changed scope or negation. "
            "No warnings is not approval. These review diagnostics neither alter the manifest nor block submission."}
