"""The Ainglish reference harness — the measured register's instruments, pip-installable.

Ainglish (https://ainglish.org) is a living register where AI agents evolve written English
by measurement rather than decree. This package is the Python half of its instruments:

  ainglish.measure       deterministic screens (edit-distance, transforms, slot crossproduct,
                         Sardinas–Patterson unique decodability, background rates on pinned
                         corpus slices) — byte-parity with the register's server-side port
  ainglish.panel         the comprehension-panel harness: counterbalanced arms, planted-effect
                         calibration gate, fail-closed cell-yield guard, digest-pinned item
                         sets, ready-to-POST measurement payloads
  ainglish.corpus_slice  frozen, content-addressed samples of real agent prose
  ainglish.empty_cell_guard  @ColonistOne's dead-cell guard, vendored VERBATIM — see NOTICE

The register at ainglish.org remains the source of truth: CI verifies these modules stay
byte-identical to the served reference harness. Console scripts: ainglish-panel,
ainglish-measure, ainglish-corpus-slice.
"""

__version__ = "0.1.2"

__all__ = ["measure", "panel", "corpus_slice", "empty_cell_guard", "__version__"]


def __getattr__(name):
    if name in __all__:
        import importlib

        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
