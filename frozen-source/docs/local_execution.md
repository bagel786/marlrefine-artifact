# Local non-preregistered execution route

This route is the one selected for the study run. It deliberately does not use
Zenodo. It preserves a clean pre-result Git commit and exact hash bindings, but
it is **not** a preregistration and supplies no independent public timestamp.

Before any reserved game or mutation candidate runs:

1. commit all executable/protocol changes as source commit A;
2. build and test the Linux/arm64 container from A;
3. generate the sealed mutation manifest first and the study manifest second,
   both bound to A, then commit those generated files as B;
4. create an external `marlrefine_local_execution_authorization` JSON object
   with `experiments/write_local_execution_authorization.py`; and
5. verify the authorization inside the same container before running a batch.

The local authorization records the manifest, source-tree, dependency-lock, and
source-commit identities. It also records `preregistered=false` and
`public_archive=false`. The existing artifact field name `receipt_sha256` is
retained for schema compatibility; under this route it contains the SHA-256 of
the local execution authorization, not a Zenodo receipt.

All primary, external-baseline, mutation, replay, and analysis commands use
`--execution-authorization`. Results remain local until the scientific paper is
complete. Any publication or venue-specific archive is a later submission
decision and must not be described retroactively as pre-registration.
