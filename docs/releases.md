# Release preparation and approval

A release is a source-only archive produced from a reviewed commit, with
rendered API documentation added. The archive includes the committed module
metadata and licenses; it does not include Git metadata, untracked files,
downloaded runtimes, installed packages, or local build caches.

## Prepare a candidate

Commit the candidate and run from a clean tracked worktree (local Bazel
lockfile refreshes are allowed and excluded from the archive):

```sh
python3 tools/prepare_release.py > /tmp/rules_bun-release-notes.md
```

The helper obtains the module name/version from Bazel, generates API Markdown
with Stardoc, packages committed source, and verifies the exact archive using
the independent consumer with development dependencies disabled. The consumer
also runs after its producer state is deleted. Generated files go under `dist/`:
the versioned archive, `SHA256SUMS`, and `source.json` with the source commit,
archive digest/integrity, and strip prefix. Release notes on stdout include a
copyable `bazel_dep` plus `archive_override` for the intended release URL.
That URL becomes usable only after publication; it is not a BCR declaration.
Preparation logs go to stderr.

An optional argument such as `v0.1.0` must match Bazel's module version. The
helper packages committed source even when Bazel updates its local lockfile;
review and commit dependency changes before preparing the final candidate.
Tar ownership/timestamps and gzip metadata are normalized so a candidate's
archive bytes do not depend on the preparing host or commit timestamp.
`source.json` records the actual commit separately.

Normal CI verifies the prepared archive on its native platform matrix. Review
the candidate contents, integrity, license notices, generated API reference,
release notes, and platform logs before approving a version tag. Security and
repository settings must still match the documented release policy. A green
candidate is evidence for review, not permission to publish.

## Create the approved draft

After maintainer approval, create a fixed `vMAJOR.MINOR.PATCH` tag at the
reviewed commit on `main` and push that tag. Never move or replace a fixed tag.
The [release workflow](../.github/workflows/release.yaml) checks main-branch
ancestry, runs the normal CI workflow, and invokes the pinned
[rules-template reusable release workflow](https://github.com/bazel-contrib/.github/blob/v7.7.0/.github/workflows/release_ruleset.yaml).
It prepares the archive again, verifies it, adds provenance attestations, and
uploads the release files to a draft. A failed workflow is investigated against
that exact tag; do not repair source by moving the tag.

This adapts the template's release stage without its BCR publish/finalize
stages. The fixed entrypoint `.github/workflows/release_prep.sh` is only an
exec wrapper for the Python helper. The template's ordinary Bazel test command
is replaced by API generation because acquisition tests run outside the Bazel
test sandbox; the helper and CI exercise the actual archive.

## Publish after review

Inspect the draft assets and provenance, verify the downloaded archive against
`SHA256SUMS` and the reviewed candidate, and confirm the installation snippet's
URL, integrity, strip prefix, and module version match the asset. Obtain the
maintainer's explicit publication approval, then publish the draft. Immutable
releases and fixed-tag protection prevent rewriting published bytes. Fix a
published defect with a new version.

Verify the published asset through the independent consumer and update its
example pin to the release URL/integrity in a follow-up reviewed change. Link
the release, source commit, platform CI, and independent-consumer evidence to
the readiness issue. BCR preparation/submission remains in
[issue #4](https://github.com/curlewlabs-com/rules_bun/issues/4).

## CI cost and storage

CI uses standard GitHub-hosted runners for this public repository, with Bazel
cache uploads disabled. Normal pull-request verification uploads no artifacts.
The reusable release workflow uploads only the small source/documentation
archive, metadata, notes, and attestations; downloaded runtime packages and
build caches are not release assets. Review the measured candidate size before
each release, and reassess storage and metered runner use before changing that
scope. Release preparation does not require a hosted documentation site.
