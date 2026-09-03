# Sealed reviewer package

Build this package only after the archive-gated study, external baselines,
mutation experiment, frozen analysis, and manual adjudication are complete. The
builder does not run any experiment and does not discover files. It accepts one
explicit, hash-pinned allowlist and fails closed on anything unbound.

## Inventory

Create a private inventory JSON under the result workspace. Every `path` is a
canonical POSIX path relative to `--root`, every `sha256` is required, and every
role is unique. The fifteen singleton roles are:

- `pre_run_bundle`, `pre_run_identity`, `archive_receipt`, and
  `archive_gate_log`;
- `raw_batch`, `external_baselines`, and `mutation_batch`;
- `frozen_analysis`, `latex_macros`, and `manual_adjudication`;
- `container_identity`, `container_image_archive`, `reproduction_readme`,
  `deviation_log`, and `run_diary`.

Add any number of separately reviewable evidence artifacts with unique roles of
the form `evidence:<stable-name>`. Unknown singleton roles, duplicate paths or
roles, missing roles, absolute/traversing paths, symlinks, and workstation paths
are rejected.

```json
{
  "artifact_type": "marlrefine_reviewer_package_inventory",
  "entries": [
    {
      "path": "dist/marl-adapter-conformance-protocol-v1.1.tar.gz",
      "role": "pre_run_bundle",
      "sha256": "<64 lowercase hexadecimal characters>"
    },
    {
      "path": "results/root-001-replay.json",
      "role": "evidence:root-001-replay",
      "sha256": "<64 lowercase hexadecimal characters>"
    }
  ],
  "schema_version": 1
}
```

The complete inventory must include all fifteen singleton entries; the two-row
fragment above only illustrates the field format. Evidence hashes referenced by
every retained root (confirmed or rejected) and frozen control must resolve
either to an inventory file or to a regular file inside the pre-run protocol
bundle. Every confirmed root's `causal_patch.patch_sha256` must resolve to its
allowlisted isolated diff at the exact `evidence_reference` path. The file must
have a nonempty unified-diff or Git-binary-diff envelope; a matching hash on
arbitrary text is rejected. Logical localized-witness and source-tree digests
are not treated as standalone evidence-file hashes.

Preserve the exact successful `verify-archive` stdout as `archive_gate_log`;
the builder expects the single `verified ... prospective_cases=840` line, not a
retyped summary. Use `run_diary` as the chronological binding index for every
execution log and output. Add each standalone execution log with a unique
`evidence:<stable-name>` role. In the diary, record only repository-relative or
container paths plus a non-PII volume label or stable backup alias. Do not put
`/Users/...`, `/home/...`, temporary host paths, phone numbers, or account names
in the sealed diary. Canonical fake paths used by negative tests, such as
`/Users/example/...`, are recognized as fixtures rather than host provenance.
The verifier enforces portable paths and known machine-path patterns; it is not
a general PII detector and does not prove that an arbitrary GitHub account or
repository name is anonymous. Using aliases such as
`private-github-release/protocol-v1-image` is a procedural operator duty.

The package contains a **pre-seal diary snapshot**, so it must end with
`Status: complete through final analysis` and completed entries for
`verify-archive`, the prospective batch, external baselines, mutation,
preliminary analysis, and final analysis. Preliminary analysis must omit manual
adjudication. Final analysis must bind the complete manual-adjudication input;
its two outputs are the frozen analysis and LaTeX macros. Standalone `replay`
and `evidence development` entries are allowed between those analyses and must
be recorded whenever invoked. Every pre-seal command must use the canonical
`docker run [--rm] [--mount <spec> ...] <image> <command>` form (with
`--mount=<spec>` also accepted), and the actual `<image>` operand must be the
schema-v2 image ID—not a later command argument, tag, host-only `uv run`, or
shell variable. The study identity also records Docker Engine 29.5.2 with the
containerd image store and the exact image platform.

Record the later package-sealing event next to the separate package identity,
outside the archive; putting the package's own hash in its diary would be
self-referential. The deviation log must end in either `Status: no
deviations` or `Status: complete` with unique IDs, UTC timestamps, and filled
disposition fields. The reproduction README must say `Status: verified` and
retain the stable `Reproduction README`, `Clean-environment command`, and
`Expected hashes` headings with a real command and the exact paths and SHA-256s
of the bound artifacts. Blank templates and placeholder tokens fail.

## Build and verify

Run from the repository root, substituting the private result-workspace paths:

```bash
uv run python deposit/build_reviewer_package.py build \
  --root . \
  --inventory results/reviewer_inventory.json \
  --output dist/marlrefine-reviewer-package-v1.tar.gz \
  --identity-output dist/reviewer_package_identity.json

uv run python deposit/build_reviewer_package.py verify \
  --archive dist/marlrefine-reviewer-package-v1.tar.gz \
  --identity dist/reviewer_package_identity.json
```

The archive is a deterministic gzip/PAX tar with normalized metadata. It
contains only `MANIFEST.json`, `SHA256SUMS`, and the allowlisted payload under a
single fixed prefix. The separate identity JSON hashes the finished archive and
manifest and maps every role to its path and digest. It is intentionally not
inside the archive, avoiding a self-reference.

The independent verifier checks the gzip/tar structure, member order and
metadata, rejects appended or concatenated gzip data, checks the exact manifest
allowlist, all sizes and hashes, the checksum file, and the separate identity.
It first validates the nested pre-run bundle as the canonical protocol-v1.1
freeze: one canonical gzip stream, canonical tar ordering and metadata, exact
internal `SHA256SUMS` and `FREEZE_METADATA.json`, exact source inventory and
source-tree digest, manifest/mutation/lock bindings, and canonical archive
reconstruction. The standalone container identity must be byte-for-byte equal
to `container/IMAGE_IDENTITY.json` in that bundle, and both must follow the
exact image-identity schema. The receipt must follow its exact offline schema
and bind the canonical Zenodo record ID, DOI, URL, UTC publication time,
protocol bundle, and protocol identity.

The required `container_image_archive` is the uncommitted Docker image archive
whose filename, format, byte size, and SHA-256 were committed before
the run by schema-v2 `IMAGE_IDENTITY.json` and `FREEZE_METADATA.json`. The
verifier requires both commitments to agree with the reviewer payload, checks
the archive's byte identity, and independently validates its exact safe OCI
layout. Under the pinned Docker 29.5.2 containerd store, the recorded image ID
is the selected OCI manifest digest. That manifest binds the separately
recorded config digest and ordered layer content; config/rootfs diff IDs,
platform, nested layer-tar safety, and compressed and expanded bounds are also
checked. Repository digests are optional supplemental registry identities and
are not interchangeable with either manifest or config digest.

The verifier then requires the exact frozen analysis schema/analysis ID and
checks every analysis input identity. It runs the analyzer and LaTeX renderer
from the source extracted from the validated pre-run bundle—not from a mutable
ambient checkout—and requires exact object and byte equality. The same frozen
source computes the live verifier runtime; its source, lock,
package/distribution bytes, Python, platform, and Git/null-Git identity must
equal the recorded batch and analysis runtimes except for their explicit
timestamps. Run build and verify in the exact final study image/environment:
`linux/arm64` on Docker Engine 29.5.2 with its containerd image store, addressed
by the recorded literal OCI manifest image ID.
The pre-run bundle/identity/receipt, exact successful 840-case gate log,
complete diary and adjudication state, and evidence closure are checked as one
connected identity graph. Keep both output files together for review; any
changed input requires a fresh build and identity.

Before staging, the builder totals the allowlisted payload and requires free
space for at least two uncompressed payload copies plus fixed headroom. The
verifier repeats the check for its extraction volume and caps the declared
payload at 128 GiB. This complements, rather than replaces, the runbook's 50 GiB
mandatory / 100 GiB preferred pre-run capacity gate.

## Post-seal paper overlay

The analysis macros deliberately leave the private reviewer URL and reviewer
package revision pending. After the two package files are sealed and placed at
their actual private review URL, generate a separate overlay:

```bash
uv run python deposit/build_reviewer_package.py render-paper-identity \
  --archive dist/marlrefine-reviewer-package-v1.tar.gz \
  --identity dist/reviewer_package_identity.json \
  --review-url 'https://<private-review-service>/<submission>' \
  --output paper/reviewer_package_identity.tex
```

The command first runs the full package verifier, accepts only a safe absolute
HTTPS URL, and writes `ReviewerPackageURL` plus the exact archive SHA-256 as
`ReviewerPackageRevision`. Load this file after the generated result macros in
the private submission build. `paper/main.tex` does this automatically when the
ignored overlay is present and remains unchanged when it is absent. The overlay
must remain outside the reviewer archive: the separate identity hashes the
archive, so adding it to that archive would create a circular identity. Refuse
edits to a sealed overlay; regenerate it only for a newly sealed package or
changed private review URL.
