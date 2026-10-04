# Contributing

Use the Bazel release in `.bazelversion`, Python, and Git. The integration
checks download their own Bun and Node runtimes. Keep changes on a branch and
open a pull request with the behavior change and verification evidence.

## Maintainership and review

Curlew Labs maintains this project. Jeffrey Wall
([jeffwall-curlewlabs](https://github.com/jeffwall-curlewlabs)) reviews changes
and approves releases. Use issues for reproducible defects and proposals;
use [private vulnerability reporting](SECURITY.md) for security concerns.

Changes land through pull requests with passing CI and maintainer review.
Public API changes must follow the [compatibility policy](docs/compatibility.md)
and update consumer documentation and behavior coverage in the same change.
Fixed version tags and published release artifacts must not be replaced.

## Local checks

Install `pre-commit==4.3.0`, then run:

```sh
pre-commit install
pre-commit run --all-files
bazel build //bun:api_docs
python3 tests/check.py
python3 tests/check_consumer.py
```

The archive check packages committed `HEAD`, so commit the candidate before
using its result as release evidence. The independent consumer README also
describes verification of a prepared archive. Network access is required for
cold acquisitions. Avoid concurrent Bazel commands against the same output base.

The `api_docs` target extracts the public repository rule's documentation into
Bazel's Stardoc-compatible binary proto. It keeps the attribute contract next
to its implementation; the README explains the acquisition/execution boundary.
A rendered documentation site and release documentation bundle are not yet
configured.

## Ruleset structure

[Bazel's distribution guidance](https://bazel.build/versions/9.0.0/rules/deploying)
and [rules-template](https://github.com/bazel-contrib/rules-template) are the
baseline for repository layout, contribution tooling, documentation extraction,
and CI. This repository uses the template's separation between ruleset checks
and a nested independent consumer, buildifier through pre-commit, and a stable
CI conclusion job. `.bazelignore` keeps the consumer module out of root target
traversal.

The adaptations reflect this ruleset's scope:

- `bun/repositories.bzl` remains the public acquisition entrypoint. There are
  no build/test rules or registered runtime toolchains; callers supply runtimes.
- The runnable external module lives in `examples/consumer` so users can copy
  it. Its archive check uses a temporary local registry to keep dev dependencies
  disabled even before publication to BCR.
- Repository acquisition checks invoke real Bazel processes outside a Bazel
  test sandbox. They need network downloads and must also inspect failed
  repository evaluation, which an ordinary successful build does not cover.
- CI tests the selected Bazel version on native Linux/macOS hosts. Additional
  Bazel versions need evidence before becoming a compatibility claim.

## Releases

Public source is not a versioned release. No automatic tagging or publishing
workflow is configured. Release preparation remains in
[issue #1](https://github.com/curlewlabs-com/rules_bun/issues/1): use the
rules-template release workflow as the starting point, verify the source-only
archive with the external consumer, and have the maintainer approve the
candidate before creating a release. Preserve MIT licensing and all applicable
third-party notices when adopting upstream release code.

BCR metadata, presubmit configuration, and publishing automation belong to the
separate future [issue #4](https://github.com/curlewlabs-com/rules_bun/issues/4).
