# Deviation log template

Create one entry before rerunning an interrupted authorization-gated stage or whenever
execution differs from the frozen protocol or runbook. Do not enter scientific
outcomes in a blank template.

For the final pre-seal snapshot, add exactly one final marker near the top:
`Status: no deviations` if there were none, or `Status: complete` after every
entry below has a nonblank deviation ID and disposition. A blank template is
not a complete deviation log.

## Deviation entry

- Deviation ID:
- Recorded at (UTC):
- Operator:
- Affected stage and run-diary entry:
- Trigger (`interruption`, infrastructure failure, protocol amendment, or
  other):
- Planned procedure:
- Observed operational departure:
- Whether any named artifact was published:
- Affected paths and SHA-256 values, or `none`:
- Whether any result content was inspected before the decision:
- Preservation/disposition of prior files:
- Proposed corrective action:
- Exact rerun command and new output path, if applicable:
- Cases or records affected:
- Confirmatory or exploratory classification after correction:
- Rationale for that classification:
- Approval and date:
- Links to superseding artifacts or protocol amendment:

Append entries chronologically. Never edit an earlier entry to make a rerun
appear to be the original execution.
