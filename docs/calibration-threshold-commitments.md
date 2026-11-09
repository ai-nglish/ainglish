# Portable calibration thresholds

The panel accepts a finite declared calibration threshold such as `0.95`.
Previously its emitted manifest kept that as a binary float, which the SDK's
commitment guard correctly refused on PHP hosts with different
`serialize_precision` settings. This could block a valid prospective panel before
minting. It did not justify running the panel first or weakening the threshold.

The emitted calibration identity now preserves historically portable numeric
values (`0.125`, `0.5`, etc.) and represents other decimals as exact strings
(`"0.95"`). The existing numeric gate still compares the same value, and observed
calibration results retain their numeric form. Legacy absolute-gap rules still
carry `min_recovered: null`. No threshold is rounded, inferred or relaxed.

Both comprehension/entropy/learnability and robustness manifest producers use
the helper. Tests exercise the actual mint-before-reader path, commitment equality
between the planned and filed manifest, unchanged historic identities and the
learnability preparation which exposed this defect. The six message-blind-floor
parity blocks are unchanged; their producer pin must follow the edited file and
be mirrored byte-for-byte in Symfony when the SDK harness is published/served.
