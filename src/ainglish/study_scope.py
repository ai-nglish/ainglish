"""Optional experiment-purpose declarations. No inference, API or governance changes."""
from copy import deepcopy
from .panel import study_scope_fields


def attach(manifest, *, purpose, scope):
    """Return a detached prospective manifest/runspec with a bounded scope note.

    Attach before deriving or minting the manifest. Never modify a frozen or
    already-submitted experiment to change its declared purpose retrospectively.
    A declaration is the author's statement, not a certification of claim coverage.
    """
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError("manifest must be a non-empty object")
    if {"study_purpose", "study_scope"} & set(manifest):
        raise ValueError("manifest already carries study scope; refusing overwrite")
    fields = study_scope_fields({"study_purpose": purpose, "study_scope": scope})
    return dict(deepcopy(manifest), **fields)


def inspect_manifest(manifest):
    """Report absent/malformed declarations without inventing a study's purpose."""
    try:
        fields = study_scope_fields(manifest)
    except ValueError as exc:
        return {"status": "malformed", "report_only": True, "error": str(exc)}
    return {"status": "declared" if fields else "undeclared", "report_only": True, **fields}
