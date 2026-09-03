# Frozen manifest staging directory

This source-controlled file keeps the `manifests/` directory present in clean
source commit A and in the source-A container. The two JSON manifests are
generated evidence and are deliberately absent from A.

After A is clean and its pinned container passes, generate
`mutation_v1.json` first with `experiments/write_mutation_manifest.py`. Then
generate `study_v1_draft.json` with `experiments/write_draft_manifest.py`; the
second generator validates and binds the exact mutation-manifest SHA-256.
Neither generator constructs a game or executes a candidate, control, or
prospective trace.

Commit both generated JSON files, together with the five frozen discovery
artifacts, in generated-evidence commit B. Add `container/IMAGE_IDENTITY.json`
only after the final B image passes, then amend B as described in
`deposit/README.md`.
