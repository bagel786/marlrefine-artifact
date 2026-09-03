# Run diary template

Use one copy for the archive-gated study. This template records operations and
identities, not scientific outcomes. Use UTC throughout and leave no field
implicit.

Before reviewer-package assembly, replace this paragraph with `Status: complete
through final analysis`. The sealed package contains that pre-seal snapshot.
Record reviewer-package sealing and its resulting hash outside the archive,
next to the separate package identity, to avoid a self-reference.

Use repository-relative paths or container paths. For separate storage, record
a non-PII volume label or stable backup alias, never an absolute `/Users/...`,
`/home/...`, temporary-host path, account name, or private mount path.
Use a procedural alias such as `private-github-release/protocol-v1-image` for
the exact-image backup; the reviewer validator is not a general PII detector.

## Study identity

- Protocol record URL/DOI:
- Archive receipt path and SHA-256:
- Source commit A:
- Generated-evidence commit B:
- Container image ID:
- Container image archive path and SHA-256:
- Docker execution engine/store: Docker Engine 29.5.2 with containerd image store
- Container image platform:
- Exact image backup alias and round-trip SHA-256:
- Operator:
- Primary result path (repository-relative or container path):
- Free space before first gated command (GiB):
- Separate backup target (volume label/stable alias plus relative path):
- Backup capacity verified at (UTC):
- Deviation-log path:

## Run entry

Use exactly one of these pre-seal stage values: `verify-archive`, `prospective
batch`, `external baselines`, `mutation`, `preliminary analysis`, `replay`,
`evidence development`, or `final analysis`. Use `reviewer-package sealing`
only in the separate post-seal record. The preliminary-analysis command omits
`--manual-adjudication`; the final-analysis command includes it and binds the
complete manual file as an input. Replay and evidence-development entries are
optional only when no such invocation occurred.

Every pre-seal `Exact command` must use the canonical runbook form `docker run
[--rm] [--mount <spec> ...] <image> <command>`. The `<image>` operand—not a
later command argument—must be the literal schema-v2 container image ID. An
equivalent `--mount=<spec>` spelling is accepted. Do not record another Docker
option before the image, a mutable tag, host-only `uv run`, `$RUN_IMAGE`, or
another unexpanded shell variable.

- Entry ID:
- Stage:
- Started at (UTC):
- Ended at (UTC):
- Exact command:
- Input paths and SHA-256 values:
- Intended output path(s):
- Exit code or interruption signal:
- Completion state (`completed` or `interrupted`):
- Published output path(s), or `none`:
- Published output SHA-256 values, or `none`:
- Backup copy path(s):
- Backup hash verification:
- Operational notes:
- Linked deviation ID(s), or `none`:

Repeat the run-entry section for every invocation. Never replace an earlier
entry, and never infer a completed artifact from console output alone.
The pre-seal snapshot must contain completed entries named exactly
`verify-archive`, `prospective batch`, `external baselines`, `mutation`, and
both `preliminary analysis` and `final analysis`. A blank template is not a
review-ready diary.
