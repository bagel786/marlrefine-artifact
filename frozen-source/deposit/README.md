# Manual public, timestamped, hash-bound Zenodo pre-run deposit

Prospective semantic execution remains disabled until this bundle is manually
published on Zenodo and the resulting record identity is written into the
archive receipt consumed by the guarded runner.

Record title:

> Phase-Aware Source-Aligned Conformance Testing for Multi-Agent Environment
> Adapters: Prospective Protocol and Study Manifests, Version 1.1

The freeze uses an explicit two-commit identity model to avoid the impossible
requirement that a committed manifest contain the hash of the commit that
contains itself:

The exact study image is `linux/arm64` and its execution engine is Docker
Engine 29.5.2 with the containerd image store. On that store, `.Id` is the OCI
manifest digest recorded as the schema-v2 image ID; the schema also records the
distinct image-config digest. A different Docker store may address loaded bytes
by config digest or tag and is not the pinned prospective engine.

### Forward correction when A/B already exist

Do not rewrite an existing freeze. Move the unpublished old Zenodo pair into a
clearly stale quarantine directory, then make source commit **A′** as a normal
child of the existing B. A′ contains all source/protocol/documentation changes
and deletes these old generated paths:

- `manifests/mutation_v1.json`;
- `manifests/study_v1_draft.json`;
- `artifacts/discovery_api_baselines.json`;
- `artifacts/discovery_controls.json`;
- `artifacts/discovery_repairs.json`;
- `artifacts/pilot.jsonl`;
- `artifacts/registry_census.json`; and
- `container/IMAGE_IDENTITY.json`.

Use A′ everywhere the numbered workflow below says A. Regenerate the first
seven paths, commit them as candidate **B′**, build/test/export its exact image,
then force-add the eighth path and amend only B′. Prove that the nonrecursive
`A′..B′` diff contains exactly eight `A` entries and no other path. Push A′/B′
as a normal fast-forward. Never amend, reset, or force-push the earlier A/B.

1. Replace every remaining `REPLACE_BEFORE_DEPOSIT` value in
   `zenodo_metadata.json` and finish every executable/protocol/documentation
   source change. Software is Apache-2.0 under `LICENSE`; documentation/data
   are CC BY 4.0 under `LICENSE-docs-data`. The JSON file is a manual-entry
   checklist, not a Zenodo API request body. In Zenodo's current web form,
   declare both `apache-2.0` and `cc-by-4.0`, retain the file-specific scope
   statements, and include both license files. Do not use the legacy
   single-license deposition field for this mixed bundle; split records are the
   fallback if a future interface cannot represent both licenses.
2. Run the complete tests and commit those source inputs as commit **A**. The
   worktree must now be clean.
3. Record A with `SOURCE_A="$(git rev-parse HEAD)"`, build the pinned
   `marl-adapter-conformance:source-a` image, and run its tests. Inside that
   image, generate `manifests/mutation_v1.json` **first** and
   `manifests/study_v1_draft.json` **second**, both as
   `frozen_pending_archive` candidates using the same
   `--source-git-revision "$SOURCE_A"` and writable manifest mount:

   ```bash
   docker run --rm \
     --mount type=bind,src="$PWD/manifests",dst=/artifact/manifests \
     marl-adapter-conformance:source-a \
     uv run python experiments/write_mutation_manifest.py \
       --status frozen_pending_archive \
       --source-git-revision "$SOURCE_A"
   docker run --rm \
     --mount type=bind,src="$PWD/manifests",dst=/artifact/manifests \
     marl-adapter-conformance:source-a \
     uv run python experiments/write_draft_manifest.py \
       --status frozen_pending_archive \
       --source-git-revision "$SOURCE_A" \
       --mutation-manifest manifests/mutation_v1.json
   ```

   The second generator validates the first manifest against the frozen code
   and environment, then binds its exact SHA-256 in the main study manifest.
   Both generators record A with `git_dirty: false`; the bundle builder later
   corroborates A against B's immediate parent. This avoids a nonfunctional
   `.git`-only mount and freezes the Linux interpreter plus installed
   distribution bytes actually used for prospective execution.
4. Still in the source-A image, mount the frozen manifest read-only and
   `artifacts/` writable, then run all five discovery generators:

   ```bash
   docker run --rm \
     --mount type=bind,src="$PWD/manifests",dst=/artifact/manifests,readonly \
     --mount type=bind,src="$PWD/artifacts",dst=/artifact/artifacts \
     marl-adapter-conformance:source-a \
     uv run python experiments/run_registry_census.py
   docker run --rm \
     --mount type=bind,src="$PWD/manifests",dst=/artifact/manifests,readonly \
     --mount type=bind,src="$PWD/artifacts",dst=/artifact/artifacts \
     marl-adapter-conformance:source-a \
     uv run python experiments/run_discovery_controls.py
   docker run --rm \
     --mount type=bind,src="$PWD/manifests",dst=/artifact/manifests,readonly \
     --mount type=bind,src="$PWD/artifacts",dst=/artifact/artifacts \
     marl-adapter-conformance:source-a \
     uv run python experiments/run_discovery_api_baselines.py
   docker run --rm \
     --mount type=bind,src="$PWD/manifests",dst=/artifact/manifests,readonly \
     --mount type=bind,src="$PWD/artifacts",dst=/artifact/artifacts \
     marl-adapter-conformance:source-a \
     uv run python experiments/run_discovery_repairs.py
   docker run --rm \
     --mount type=bind,src="$PWD/manifests",dst=/artifact/manifests,readonly \
     --mount type=bind,src="$PWD/artifacts",dst=/artifact/artifacts \
     marl-adapter-conformance:source-a \
     uv run marlrefine probe --allow-violations
   ```

5. Commit exactly the two frozen manifests and five discovery artifacts
   as candidate commit **B**. These seven generated files are
   `manifests/mutation_v1.json`, `manifests/study_v1_draft.json`, and the five
   files listed by `DISCOVERY_ARTIFACTS` in `build_protocol_bundle.py`. Do not
   amend A and do not include source changes in B. Build and test the pinned
   final container from this candidate B.
6. Write `container/IMAGE_IDENTITY.json` while exporting the exact verified
   image outside the public two-file `dist/` inventory:

   ```bash
   uv run python container/write_image_identity.py \
     --image marl-adapter-conformance:protocol-v1 \
     --archive-output \
       dist/private-execution-image/marl-adapter-conformance-protocol-v1.docker.tar
   ```

   The identity proves that the final image's source, lock, Python, package
   versions, and installed distribution bytes exactly match the manifest. It
   also binds the Docker image archive's fixed filename, format,
   byte size, and SHA-256. Verification and export use the inspected immutable
   OCI manifest image ID, not the mutable tag, and record its distinct config
   digest separately. The verification-output hash normalizes only line endings
   and the recorded uv/pytest elapsed clocks. The identity file is intentionally
   ignored and excluded from both the source hash and Docker context. Add it
   explicitly with
   `git add -f container/IMAGE_IDENTITY.json` and amend B without changing its
   message. Do not require a cold rebuild to reproduce the same OCI manifest or
   config digest: build timestamps, cache state, and layer metadata can change
   them without changing the frozen scientific environment. The saved archive
   is the exact run image; a cold rebuild is only a semantic reproduction and
   must be checked through the recorded `linux/arm64` platform, source, lock,
   base, Python, package, and installed-byte identities. After the amend, the
   bundle builder requires all
   eight generated identity paths in A..B—the two manifests, five discovery
   artifacts, and `container/IMAGE_IDENTITY.json`—and rejects any other path.
   The image archive remains uncommitted, and B's immediate parent remains A.
7. Run the bundle builder on the clean amended-B host checkout with the image
   archive supplied explicitly:

   ```bash
   uv run python deposit/build_protocol_bundle.py \
     --image-archive \
       dist/private-execution-image/marl-adapter-conformance-protocol-v1.docker.tar
   ```

   Host package bytes are intentionally irrelevant here: the builder validates
   source/lock bytes, the Linux identities recorded by the manifest, every
   discovery artifact, `IMAGE_IDENTITY.json`, and the exact Docker archive
   structure/hash/size. It records the archive commitment in
   `FREEZE_METADATA.json`, plus both A
   (`source_git_revision`) and B (`archive_git_revision`) under
   `two_commit_nonrecursive_v1`, then deterministically
   builds the protocol archive first, then writes `dist/protocol_identity.json`
   from the completed archive hash. The separate protocol identity is
   intentionally not inside the archive and is excluded from the source-tree
   identity. The Docker archive bytes are also deliberately absent from the
   public protocol bundle: committing their hash preserves the pre-run image
   identity without rehosting Debian, Python, and third-party binary layers
   under the record's author-owned file licenses.
8. **Before any irreversible Zenodo publication**, create a normal,
   non-immutable private GitHub Release in the frozen private repository,
   targeting B (or B′ after a forward correction). Upload the exact Docker
   archive and a small hash/identity manifest. Download the release asset
   through a fresh authenticated path that is not the upload source, then
   require its byte size and SHA-256 to equal `IMAGE_IDENTITY.json`. Treat the
   backup as complete only after this fresh-download round trip succeeds. Record
   a stable non-PII alias such as `private-github-release/protocol-v1-image`, its
   asset-relative path, size, SHA-256, and verification UTC time. Do not upload
   the Docker archive to the public Zenodo record. Retain it for exact execution
   and for the reviewer package's required `container_image_archive` role.
9. Have a parent or guardian review the uploader terms and the public creator
   identity. Using `zenodo_metadata.json`, make the record public and upload
   **only** these two files:

   - `marl-adapter-conformance-protocol-v1.1.tar.gz`; and
   - `protocol_identity.json`.

   Confirm that no other attachment is present, then click Publish. The online
   gate verifies the expected two file identities, URLs, and hashes from the
   receipt; it does not claim to prove that the Zenodo record has no additional
   files. The two-file inventory is therefore a manual publication requirement.
   Do not use Zenodo's post-publication file-editing facility.
   Any correction must be a new, explicitly linked record version with a new
   version DOI, and its effect on confirmatory status must be documented before
   further execution.
10. Create the local receipt with the public record ID, canonical DOI and URL,
   Zenodo record creation timestamp, manifest/source/lock hashes, and the exact
   filenames and SHA-256 hashes of both uploaded files.
11. Before any archive-gated command, prepare the result volume and a separate
    backup target. At least **50 GiB free is mandatory; 100 GiB is preferred**
    on the result volume. The backup must be on a different physical volume or
    independently managed storage and must have enough capacity for the raw
    batch, external baselines, mutation batch, analysis, logs, and sealed
    reviewer package. Record the free-space measurement, primary path, backup
    stable backup alias (not a private absolute host path), UTC time, operator,
    image identity, bound Docker archive path/hash, and receipt hash in
    [`docs/run_diary_template.md`](../docs/run_diary_template.md). Include the
    exact-image release alias and fresh-download round-trip hash from step 8.
    This alias rule is procedural privacy hygiene, not a claim that the package
    validator can detect every account or repository name. Do not start a gated
    run if either the minimum free space or separate backup is absent.
12. Load the bound Docker archive on Docker Engine 29.5.2 with the containerd
    image store, confirm that its recorded `linux/arm64` OCI manifest image ID
    exists,
    then mount the receipt into that exact image and run `verify-archive`.
    This queries the public Zenodo API and downloads/hashes both deposited
    files; a locally forged receipt cannot open the execution gate.

    ```bash
    docker image load \
      --input \
        dist/private-execution-image/marl-adapter-conformance-protocol-v1.docker.tar
    docker image inspect '<sha256:image-id-from-IMAGE_IDENTITY.json>' \
      --format '{{.Id}}'
    ```

    The inspect output must exactly equal the recorded ID. Use that ID as the
    image operand below; a tag is only a convenience and must not choose the run
    image. Replace every angle-bracket token below with the literal recorded ID.
    The run diary's `Exact command` must contain that literal ID, not `$RUN_IMAGE`
    or another shell variable.

    ```bash
    docker run --rm \
      --mount type=bind,src="$PWD/RECEIPT.json",dst=/run/archive_receipt.json,readonly \
      '<sha256:image-id-from-IMAGE_IDENTITY.json>' \
      uv run marlrefine verify-archive \
        --archive-receipt /run/archive_receipt.json \
      > results/archive_gate.log
    ```

    Preserve that exact one-line stdout file without retyping it. It is the
    reviewer-package `archive_gate_log`; a successful frozen gate ends in
    `prospective_cases=840`. Record the command, exit code, relative log path,
    and log SHA-256 in the run diary.

13. In that same exact image, mount the prepared writable results directory and
    run the guarded batch:

    ```bash
    docker run --rm \
      --mount type=bind,src="$PWD/RECEIPT.json",dst=/run/archive_receipt.json,readonly \
      --mount type=bind,src="$PWD/results",dst=/results \
      '<sha256:image-id-from-IMAGE_IDENTITY.json>' \
      uv run marlrefine prospective \
        --archive-receipt /run/archive_receipt.json \
        --output /results/prospective_raw.jsonl
    ```

    The 840-case artifact appears atomically only after the quiet batch ends.
    Hash it, copy it to the separate backup target, verify the backup hash, and
    add both hashes and paths to the run diary before continuing.
14. Run the separately sealed external-baseline artifact in the same image:

    ```bash
    docker run --rm \
      --mount type=bind,src="$PWD/RECEIPT.json",dst=/run/archive_receipt.json,readonly \
      --mount type=bind,src="$PWD/results",dst=/results \
      '<sha256:image-id-from-IMAGE_IDENTITY.json>' \
      uv run marlrefine prospective-baselines \
        --archive-receipt /run/archive_receipt.json \
        --output /results/prospective_external_baselines.json
    ```

    This runs PettingZoo `api_test` once per frozen game at 1,000 cycles with
    action spaces seeded to zero. It also downloads the exact hash-pinned
    Shimmy 2.0.1 sdist, verifies the released OpenSpiel test-module bytes, and
    captures that module's pytest result. The upstream suite is contextual
    evidence, not a cohort comparator: its fixed game list differs from the 105
    games and two released paths sample unseeded action spaces.
15. After the external baselines finish, run the archive-gated sealed mutation
    cohort in the same image:

    ```bash
    docker run --rm \
      --mount type=bind,src="$PWD/RECEIPT.json",dst=/run/archive_receipt.json,readonly \
      --mount type=bind,src="$PWD/results",dst=/results \
      '<sha256:image-id-from-IMAGE_IDENTITY.json>' \
      uv run python experiments/run_mutation_study.py \
        --manifest manifests/study_v1_draft.json \
        --mutation-manifest manifests/mutation_v1.json \
        --archive-receipt /run/archive_receipt.json \
        --output /results/prospective_mutations.json
    ```

    The current external-baseline and mutation entry points publish their named
    artifacts atomically only after the entire invocation completes and refuse
    to overwrite an existing artifact. An interruption is not a partial result
    and must not be silently retried. Before any rerun, add a dated entry using
    [`docs/deviation_log_template.md`](../docs/deviation_log_template.md) that
    records the interruption, inspected state, affected paths, exact rerun
    command, and confirmatory/exploratory impact. Preserve any existing named
    artifact and choose a new output path; otherwise record that no named output
    was published. After each completed artifact, hash it, copy it to the
    separate backup target, and verify the copied hash before continuing.
16. After the raw batch, external baselines, and mutation batch are complete,
    run a **preliminary read-only analysis** in the same exact image. Include
    both secondary artifacts, omit `--manual-adjudication`, and use distinct
    preliminary filenames so this pass cannot overwrite or be mistaken for the
    final paper-generating analysis:

    ```bash
    docker run --rm \
      --mount type=bind,src="$PWD/RECEIPT.json",dst=/run/archive_receipt.json,readonly \
      --mount type=bind,src="$PWD/results",dst=/results \
      '<sha256:image-id-from-IMAGE_IDENTITY.json>' \
      uv run python experiments/analyze_prospective_batch.py \
        --batch /results/prospective_raw.jsonl \
        --manifest manifests/study_v1_draft.json \
        --archive-receipt /run/archive_receipt.json \
        --external-baselines /results/prospective_external_baselines.json \
        --mutation-batch /results/prospective_mutations.json \
        --output /results/prospective_analysis_preliminary.json \
        --latex-output /results/prospective_results_macros_preliminary.tex
    ```

    The preliminary macros deliberately leave root-level adjudication fields
    pending. Hash and back up both preliminary outputs before inspecting them;
    they are evidence-development inputs, not final manuscript results.
17. Use the preliminary finding inventory to develop the evidence required for
    complete schema-version-5 manual adjudication. Generate one archive-gated
    standalone replay per candidate root, using the exact case and violation
    index reported by the preliminary analysis. For example:

    ```bash
    docker run --rm \
      --mount type=bind,src="$PWD/RECEIPT.json",dst=/run/archive_receipt.json,readonly \
      --mount type=bind,src="$PWD/results",dst=/results \
      '<sha256:image-id-from-IMAGE_IDENTITY.json>' \
      uv run python experiments/replay_prospective_finding.py \
        --batch /results/prospective_raw.jsonl \
        --manifest manifests/study_v1_draft.json \
        --archive-receipt /run/archive_receipt.json \
        --case-id "<game>::<policy>" \
        --violation-index <index> \
        --output /results/replays/<root-or-finding-id>.json
    ```

    Then exhaustively record each finding disposition and root cluster, primary
    contract basis, replay disposition, baseline evidence, and upstream status.
    For a defect or repair claim, create an isolated treatment and preserve its
    patch identity, failing-before evidence, passing-after regression matrix,
    and reversion evidence as required by
    [Frozen prospective analysis](../docs/frozen_analysis.md). Keep the manual
    adjudication status `pending` until every finding and root requirement is
    resolved; change it to `complete` only when the schema-version-5 record is
    exhaustive and fully hash-bound. Diary every evidence-development command,
    log any interrupted rerun as a deviation, and hash and back up every retained
    replay, patch, treatment, regression, and reversion artifact.
18. After the evidence-development stage and complete batch-bound manual
    adjudication are finished, run the **final** read-only analysis in the same
    exact image. The final interface requires both secondary artifacts and the
    complete schema-version-5 adjudication:

    ```bash
    docker run --rm \
      --mount type=bind,src="$PWD/RECEIPT.json",dst=/run/archive_receipt.json,readonly \
      --mount type=bind,src="$PWD/results",dst=/results \
      '<sha256:image-id-from-IMAGE_IDENTITY.json>' \
      uv run python experiments/analyze_prospective_batch.py \
        --batch /results/prospective_raw.jsonl \
        --manifest manifests/study_v1_draft.json \
        --archive-receipt /run/archive_receipt.json \
        --external-baselines /results/prospective_external_baselines.json \
        --mutation-batch /results/prospective_mutations.json \
        --manual-adjudication /results/manual_adjudication.json \
        --output /results/prospective_analysis.json \
        --latex-output /results/prospective_results_macros.tex
    ```

    Hash and back up both analysis outputs, then record the exact input and
    output hashes in the run diary. Analysis validates recorded artifacts; it
    does not authorize or rerun a game.
19. Only after final analysis, assemble and seal the private reviewer package
    described under [Required artifacts](../docs/protocol.md#required-artifacts)
    and [Frozen prospective analysis](../docs/frozen_analysis.md). Include the
    receipt, raw batch, external and mutation artifacts, manual adjudication,
    analysis JSON and LaTeX macros, execution logs, the pre-seal run-diary
    snapshot through final analysis, deviation log,
    exact schema-v2 container identity and its bound Docker image archive,
    source/manifests, and clean reproduction instructions. Generate and verify
    a complete checksum manifest, record the sealed package hash and location,
    and copy the sealed package to the separate backup target before treating
    the study as review-ready. Do not modify a sealed package; corrections
    require a new package identity and a linked deviation entry. Record the
    sealing command and final package/identity hashes outside the sealed diary
    snapshot, next to the separate identity, so the package never contains its
    own hash.

Publishing a draft or merely reserving a DOI does not release the prospective
execution guard. The record must be public, timestamped, and hash-bound. Zenodo
can technically permit an owner to edit files for a limited period after
publication, so `immutable` is not a platform guarantee and is not claimed
here. The authors will not use that facility: corrections receive a new linked
record version and version DOI. The execution gate always downloads and hashes
the public files against the frozen receipt.

The receipt is a JSON object with `schema_version: 1`, `artifact_type:
"marlrefine_protocol_archive_receipt"`, `record_id`, `doi`, `archive_url`,
`published_at_utc`, `manifest_sha256`, `source_tree_sha256`, `uv_lock_sha256`,
and `protocol_bundle` and `identity_file` objects. Each file object contains its
Zenodo filename and lowercase SHA-256 digest. `published_at_utc` must exactly
match the public record's `created` timestamp after UTC normalization.

The source-tree hash deliberately excludes generated manifests, result
artifacts, `container/IMAGE_IDENTITY.json`, and local receipt/identity files.
Therefore source commits A and B, the tested container, and the deposited
identity all share one executable/protocol source hash, while the main manifest
hash, its bound mutation-manifest hash, and the two explicit Git revisions bind
the generated evidence exactly. The deposited source allowlist matches that
identity and adds only the two frozen manifests, five validated discovery
artifacts, and the committed final image identity. That identity and
`FREEZE_METADATA.json` commit to the uncommitted Docker archive's filename,
format, byte size, and SHA-256, while the archive bytes are retained in the
private round-trip backup and required reviewer package rather than redistributed
on the public pre-run record. The entire `paper/` tree is excluded from the
executable source identity, Docker context, and public protocol bundle. This includes manuscript
sources, bibliography, graphical-TOC sources, compiled PDFs, and all build
outputs. The root research dossier and the manuscript-development outline,
related-work memo, and private journal-fit audit are excluded for the same
reason. The manuscript and its working materials remain private until the
separate submission workflow.
