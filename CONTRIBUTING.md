# Contributing

Thanks for your interest in improving `rules_bun`. The bar for a change is
whether the same declared inputs still produce the same packages, each checked
against its locked integrity, with anything the rules cannot acquire exactly
failing instead of producing a partial result. Bug fixes, sharper refusals of
locks and inputs outside what the rules support, coverage of another platform
with the evidence the compatibility policy asks for, and clearer docs are all
welcome.

Use the Bazel release in `.bazelversion`, Python, and Git. The integration
checks download their own Bun and Node runtimes. Keep changes on a branch and
open a pull request with the behavior change and verification evidence.

## Before a large change

Propose it in an issue before writing it. The guarantees rest on rules that are
easy to weaken by accident: the
[failure contract](docs/compatibility.md#failure-contract) and
[versioning rules](docs/compatibility.md#versioning), the
[closure format](docs/closure-format.md) consumers restore from, and, for the
npm rules, the translation's model of how Bun links packages and
[what a layout must keep](docs/npm.md#what-a-layout-must-keep). A short
discussion up front saves rework.

## Maintainership and review

Curlew Labs maintains this project. Jeffrey Wall
([jeffwall-curlewlabs](https://github.com/jeffwall-curlewlabs)) reviews changes
and approves the releases that
[need approval](docs/releases.md#which-releases-need-approval). Use issues for
reproducible defects and proposals; use
[private vulnerability reporting](SECURITY.md) for security concerns.

Changes land through pull requests with passing CI and maintainer review;
[toolchain updates](#toolchain-updates) merge on passing CI alone.
Public API changes must follow the [compatibility policy](docs/compatibility.md)
and update consumer documentation and behavior coverage in the same change.
Fixed version tags and published release artifacts must not be replaced.

## Local checks

```sh
python3 -m pip install -r requirements-dev.txt
mypy
(cd tools/typecheck && bun install --frozen-lockfile && bun run typecheck)
bazel build //bun:api_docs //docs:api
python3 tests/check.py
python3 tests/check_consumer.py
python3 tests/check_state.py
```

`requirements-dev.txt` pins mypy and everything it installs.
`tools/typecheck` type-checks the npm rules' TypeScript with tsc; it is a
package of its own because the root `package.json` and `bun.lock` are the
workspace the acquisition tests install. Its pins move with
`bun add --dev --exact` there, which rewrites its `bun.lock`; keep `@types/bun`
at the Bun release `tests/deps.bzl` pins. Formatting and
linting (ruff, buildifier, shfmt, prettier, shellcheck, actionlint) run in CI's
`baseline` job, from [ci-baseline](https://github.com/curlewlabs-com/ci-baseline).

eslint runs in CI through ci-baseline. To run the same check locally, after
installing dependencies: `<a ci-baseline checkout>/lib/lint-local.sh`. It runs
the ci-baseline commit this repository's workflows pin.

The archive check packages committed `HEAD`, so commit the candidate before
using its result as release evidence. The independent consumer README also
describes verification of a prepared archive. Network access is required for
cold acquisitions. Avoid concurrent Bazel commands against the same output base.
The state check uses separate consumer modules and output bases to overlap real acquisitions;
its lifecycle barrier makes overlap independent of host speed. Its alternate
runtime pins exist to exercise byte replacement, not to expand supported
versions.

The `api_docs` target extracts the public repository rule's documentation into
Bazel's Stardoc-compatible binary proto. It keeps the attribute contract next
to its implementation; the README explains the acquisition/execution boundary.
The `//docs:api` target renders Markdown with Stardoc. Release archives include
that generated reference at `docs/api.md`; no documentation site is required.

## Toolchain updates

[Renovate](https://docs.renovatebot.com/) proposes each new Bun release, each
new minor and patch release of Node and Bazel, and each new release of the
workflow actions, once it has aged, and merges the pull request when CI passes.
A new major of Node or Bazel is moved by hand. The `# renovate:` comment above a
pin tells Renovate what the literal below is. After moving a version it runs

```sh
python3 tools/refresh_toolchain.py
```

which writes each host's Bun archive digest from the release's
`SHASUMS256.txt` and the versions and license links in the README. Run the same
command after moving a pin by hand. `MODULE.bazel.lock` is not part of it:
Bazel rewrites the host's entries on its next run. The older runtimes in
`tests/runtime_updates.bzl` do not move.

## Ruleset structure

[Bazel's distribution guidance](https://bazel.build/versions/9.0.0/rules/deploying)
and [rules-template](https://github.com/bazel-contrib/rules-template) are the
baseline for repository layout, contribution tooling, documentation extraction,
and CI. This repository uses the template's separation between ruleset checks
and a nested independent consumer, buildifier in CI, and a stable CI
conclusion job. `.bazelignore` keeps the consumer module out of root target
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

Follow [release preparation and publication](docs/releases.md) to produce a
source-only archive, verify it with the independent consumer, and review the
platform evidence. A fixed version tag triggers the rules-template reusable
workflow's draft-release stage. Publishing the draft is a separate step, taken
after the draft's own checks.

BCR metadata, presubmit configuration, and publishing automation belong to the
separate future [issue #4](https://github.com/curlewlabs-com/rules_bun/issues/4).

## License

By submitting a contribution for inclusion in this project, you agree that it
is licensed under the MIT and Apache-2.0 licenses, as the README's License
section describes, without any additional terms or conditions.
