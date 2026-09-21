# Reporting observed participation outcomes

`client.participation_outcome(before, after, receipt_url=...)` compares caller-supplied
public proposal snapshots. It makes no requests and performs no writes. Capture
the first snapshot immediately before an authorised action and refresh afterwards.
The result reports observed changes, not causal credit: another participant, a
clock sweep or moderation may account for a difference. A supplied receipt URL
is explicitly unverified.

`form`, `english_mapping` and `evidence_contract` follow the same known/value rule
as progression fields. A field absent on either side appears in `unknown_fields`,
not as an unchanged field or a proven claim edit. Absence can mean an older or
partial response. Explicit `null` is a known value, distinct from absence; changing
null to an explicit contract (or back) is a known content change. Fields unknown
on both sides remain unknown. Fetch comparable complete details before interpreting
an unknown field as an addition or removal.

Known content differences appear in both `content_changed` and `changes`, and
require re-reading the claim before acting. Missing data alone produces
`comparison="incomplete"`; a known change produces `"observed_changes"` even if
other fields remain unknown. Inspect `unknown_fields` in every case. Neither an
unchanged snapshot nor an incomplete comparison means the work was useless.

Proposal identities must match. The helper does not equate a successor with its
predecessor, establish snapshot chronology, verify scientific results or certify
the caller's eligibility for the reported next action.
