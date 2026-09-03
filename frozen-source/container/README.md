# Reproducibility container

The container uses Python 3.13.2 on Debian Bookworm Slim. The multi-platform
base-image index is pinned to:

`python:3.13.2-slim-bookworm@sha256:6b3223eb4d93718828223966ad316909c39813dee3ee9395204940500792b740`

The exact prospective execution environment is the `linux/arm64` image under
Docker Engine 29.5.2 with the containerd image store. On that pinned store,
Docker reports the selected OCI manifest digest as `.Id`. Schema-v2 identity
therefore records that manifest image ID and the distinct config digest
separately. Other engines/stores may resolve a loaded archive by config digest
or tag and are suitable only for semantic reproduction, not the frozen study.

It installs `uv==0.11.31`, resolves only from the committed `uv.lock`, installs
the editable project, and runs the complete test suite by default. The Docker
context is a default-deny allowlist matching the files covered by
`source_tree_sha256`, plus the two frozen manifests needed at run time. LaTeX build
junk, PDFs, caches, result artifacts, receipts, and image-identity files never
enter the context. The entire private `paper/` tree, including manuscript and
bibliography sources, is also outside both the source identity and container
context, as are the root research dossier and private manuscript-development
memos. Study results are not baked into the image; the guarded runner accepts
the published archive receipt and writes raw results through a mounted output
directory.

Build and verify:

```bash
docker build --pull=false -t marl-adapter-conformance:protocol-v1 .
docker run --rm marl-adapter-conformance:protocol-v1
docker image inspect marl-adapter-conformance:protocol-v1 \
  --format '{{json .RepoDigests}} {{.Id}}'
```

The manifests' Python, package versions, and installed-distribution byte hashes
are normative. Therefore both frozen manifests and every tracked discovery
artifact are generated inside the Linux source image from clean commit A, not
on the host. The generators accept A explicitly because `.git` is deliberately
absent from the Docker context; the bundle builder later proves that A is the
immediate parent of generated-evidence commit B.

From clean commit A, record its lowercase object ID, build/test the source
image, then generate the mutation manifest first and the main study manifest
second through a writable manifest mount:

```bash
SOURCE_A="$(git rev-parse HEAD)"
docker build --pull=false -t marl-adapter-conformance:source-a .
docker run --rm marl-adapter-conformance:source-a
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

The main generator refuses to freeze unless the mutation manifest has the same
source-A/environment identity, matches the declarative code, records zero
prearchive outcomes, and passes exact SHA-256 binding.

Generate discovery evidence in that same image, with the just-written manifest
read-only and `artifacts/` writable. The exact commands are listed in
`deposit/README.md`. Commit those seven generated files—the two manifests and
five discovery artifacts—as candidate B, then build and test the final image:

```bash
docker build --pull=false -t marl-adapter-conformance:protocol-v1 .
docker run --rm marl-adapter-conformance:protocol-v1
uv run python container/write_image_identity.py \
  --image marl-adapter-conformance:protocol-v1 \
  --archive-output \
    dist/private-execution-image/marl-adapter-conformance-protocol-v1.docker.tar
```

`IMAGE_IDENTITY.json` is generated only after the final B image passes. It must
bind the image's OCI manifest ID, its distinct config digest, base index,
Dockerfile, source tree, frozen manifest, Python identity, package versions,
installed distribution bytes, and the filename, size, format, and SHA-256 of an
exact `docker image save` OCI-layout archive. The writer inspects the mutable
tag once, then verifies, inspects provenance, and exports by the immutable
manifest ID. It hashes verification output only after normalizing line endings
and the recorded uv/pytest elapsed clocks; substantive test output remains
hash-bound.

Force-add the otherwise ignored identity file and amend B; it is excluded from
the source hash and Docker context. Do **not** use a cold rebuild's OCI manifest
or config digest as an equivalence test. Build timestamps, cache state, and
layer metadata can vary across otherwise equivalent cold builds. The recorded
archive is the exact prospective run image; a cold rebuild is a semantic
reproduction only when its `linux/arm64` platform, frozen source, lock, base,
Python, package, and installed-byte identities match. Repository digests are
supplementary and may be absent for a local build; registry, OCI manifest, and
config digests are distinct identities.

Before a gated run, recover and address the exact image by ID:

```bash
docker image load \
  --input \
    dist/private-execution-image/marl-adapter-conformance-protocol-v1.docker.tar
docker image inspect '<sha256:image-id-from-IMAGE_IDENTITY.json>' \
  --format '{{.Id}}'
```

The amended B contains exactly eight generated identity paths: two manifests,
five discovery artifacts, and `container/IMAGE_IDENTITY.json`. The uncommitted
Docker image archive is validated by the protocol builder and privately backed
up, but is not part of B or the public protocol bundle. The clean amended B,
whose parent is still A, is the archive revision.

## Forward correction of an existing freeze

If an earlier B already exists, preserve history. First move its unpublished
`dist/` pair into a clearly stale quarantine directory. Create forward source
commit **A′** containing every source/protocol/documentation correction and
deleting the old eight generated paths:

- `manifests/mutation_v1.json` and `manifests/study_v1_draft.json`;
- the five discovery artifacts listed by `DISCOVERY_ARTIFACTS`; and
- `container/IMAGE_IDENTITY.json`.

Build A′, regenerate the two manifests and five discovery artifacts inside its
`linux/arm64` image, and commit those seven additions as candidate **B′**. Build
and test B′, export the exact image archive, then force-add the new identity and
amend only B′. The nonrecursive `A′..B′` diff must contain exactly eight `A`
entries and no other path. Push A′/B′ as a normal fast-forward; do not amend,
reset, or force-push the earlier A/B history. Before any Zenodo publication,
follow `deposit/README.md` to back up the exact archive in a private GitHub
release and verify a fresh download by size and SHA-256.
