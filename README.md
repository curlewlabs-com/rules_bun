# Frozen Bun workspaces for [Bazel](https://bazel.build)

A repository rule for acquiring a Bun workspace before build actions run. Bazel
supplies verified Bun and Node binaries; Bun supplies frozen lockfile resolution
and platform package selection. The custom rule supplies the input layout and
exports the resulting files and symlinks.

This module is an acquisition prototype. It is not published in the Bazel
Central Registry. It does not provide build/test rules or an execution sandbox.

## Versions

The initial ruleset version is **0.1.0**. The current development toolchain is
**Bazel 9.2.0**, **Bun 1.3.10**, and **Node 24.21.0**. The pins live in
[.bazelversion](.bazelversion), [tests/deps.bzl](tests/deps.bzl), and
[MODULE.bazel](MODULE.bazel). These are the current selections, not a broader
compatibility guarantee. The [compatibility policy](docs/compatibility.md)
defines the supported baseline, public API, lifecycle/registry boundaries, and
versioning commitments.

## How this differs from Tomato's rules_bun

[Tomato's rules_bun](https://github.com/tomato-bazel/rules_bun) is MIT licensed
and already provides frozen Bun installation alongside Bun toolchain and
execution rules. Its license is suitable for reuse. This is an independent
implementation focused on acquiring selected workspace dependencies for a
consumer that restores the installed layout itself.

Compared with Tomato's
[installation rule at the reviewed revision](https://github.com/tomato-bazel/rules_bun/blob/395372ea3639805131bcc6d63a0be3166c6a3aba/bun/private/install.bzl):

- **Workspace inputs:** Tomato stages the root manifest and lockfile. This rule
  also accepts workspace manifests and installation inputs at explicit
  root-relative paths, then selects the consumer's workspace dependencies. This
  preserves the monorepo layout Bun uses for resolution.
- **Runtime ownership:** Tomato downloads Bun inside its installation rule. This
  rule accepts Bazel-acquired Bun and Node binaries from the caller and puts
  those runtimes on the installer's PATH. Lifecycle scripts that invoke Node
  therefore use the declared Node runtime.
- **Exported layout:** Tomato exposes a `node_modules/**` file group. This rule
  exports regular files throughout the acquired workspace plus a manifest of
  relative symlinks, executable modes, and content digests. Consumers can
  restore workspace-local dependencies and native outputs without relying on
  Bazel globs to follow package links.

These are acquisition requirements, not a claim that this project replaces
Tomato's broader ruleset. The tradeoff is a separate consumer-side restoration
step and a deliberately narrow API: explicit workspace directories, text
installation inputs, and native-host acquisition. See [Consume](#consume) for
the execution boundary and [Verify](#verify) for the exercised behavior.
Improvements suitable for Tomato should be considered for upstream contribution;
maintaining an independent implementation is not itself a goal.

## Use

Start with the [independent consumer](examples/consumer/README.md) for a
runnable module using a verified source archive and consumer-owned runtimes.

Load `bun_install` from `//bun:repositories.bzl` through `use_repo_rule` or your
module extension. The runnable setup in [MODULE.bazel](MODULE.bazel) and
[tests/deps.bzl](tests/deps.bzl) acquires Bun with Bazel's `http_archive` and
Node with `rules_nodejs`. The module itself has no non-dev Bazel dependencies.

```starlark
bun_install = use_repo_rule("@curlewlabs_rules_bun//bun:repositories.bzl", "bun_install")
bun_install(
    name = "npm_tools",
    bun = "@bun_archive//:bun",
    node = "@nodejs_darwin_arm64//:bin/nodejs/bin/node",
    package_json = "//:package.json",
    lock = "//:bun.lock",
    inputs = {"//:packages/tool/package.json": "packages/tool/package.json"},
    workspaces = [".", "packages/tool"],
)
```

The runtime attributes must identify source files in acquired repositories, not
aliases or action-generated executables. Select binaries for the acquisition
host; this rule does not cross-install packages for another platform.

Declare every root workspace manifest even when selecting only one consumer.
Workspace paths must be explicit directories in the root manifest: workspace
globs are deliberately unsupported. `inputs` also accepts text installation
configuration, patches, and source files needed by trusted lifecycle scripts,
with root-relative destinations. Bun reads the original manifests and committed
lock; the rule does not translate either format. Runtime execution must include
separately declared workspace sources when packages link to local code.

Acquisition has a private HOME, temporary directory, and package cache. Its PATH
contains the declared Bun and Node. It runs Bun's frozen installer with copying
instead of shared-cache hardlinks, retaining native optional packages and
trusted lifecycle output. No ambient executable directories are added to PATH.
Lifecycle programs can still access absolute host paths; acquisition is not
sandboxed. The operating system and its libraries are supplied by the
acquisition host. User environment variables and global registry credentials are
not passed to the installer.

## Consume

See the [closure format contract](docs/closure-format.md) for field semantics
and consumer responsibilities.

`@npm_tools//:files` contains the installed regular files and `closure.json`.
The manifest records installer, input, and runtime SHA-256 digests, selected
workspace paths, and the acquired tree entries. An entry is a regular file with
its SHA-256 and executable bit, a directory, or a relative symlink. Entry paths
are relative to the workspace root; exported regular file labels have a
`workspace/` prefix.

A consumer declares both the files and the manifest as inputs and restores their
layout in a private workspace. Symlinks are described separately so Bazel does
not traverse workspace or package-link cycles. Absolute, escaping, and dangling
links fail acquisition, as do nested Bazel BUILD files. See
[tests/verify.mjs](tests/verify.mjs) for an actual relocation followed by native
package execution under Node and Bun.

Regular files are made read-only. Directories remain writable for Bazel's
repository management; an executor must supply its own read-only input boundary.
Acquisition is network-enabled. Consumer actions must disable auto-install and
supply their own qualified runtime, network policy, and execution identity. The
content manifest is not a complete execution-platform identity, and this rule
alone does not establish hermeticity or qualify reusable action results.

## Verify

Use the Bazel release selected by `.bazelversion` and Python from your
development environment:

```sh
python3 tests/check.py --bazel /path/to/bazel
python3 tests/check_consumer.py --bazel /path/to/bazel
```

The integration check acquires real packages, restores their exported payload,
resolves a workspace dependency, and runs native esbuild with both runtimes. It
verifies that a workspace-only consumer cannot resolve root tooling, that
removing the native executable causes failure, and that acquisition rejects a
changed manifest against a frozen lock or an omitted workspace manifest.
Acquisition needs network access on a cold cache.

The archive check exercises an independent module with development dependencies
disabled, lifecycle output, native execution after removing acquisition state,
and corrupt/missing payload rejection. It packages committed `HEAD`; commit
candidate changes before using it as evidence. See the consumer README for
testing a prepared source archive.

The [integration workflow](.github/workflows/ci.yaml) runs these checks
on Linux and macOS, on x64 and arm64. Its
[run history](https://github.com/curlewlabs-com/rules_bun/actions/workflows/ci.yaml)
records the tested revision and platform results. A platform selection is not a
promise about other OS releases or runtime versions. Changed installer/runtime
invalidation and independent concurrent acquisitions still need directed
regression coverage; readiness is tracked in
[issue #1](https://github.com/curlewlabs-com/rules_bun/issues/1).

## Licensing and prior work

This ruleset is original code under the [MIT license](LICENSE). No runtime or
npm package binaries are vendored. Downloaded artifacts retain their own
licenses; the ruleset's license does not relicense them.

The integration example uses Apache-2.0 licensed
[rules_nodejs](https://github.com/bazel-contrib/rules_nodejs/blob/v6.7.5/LICENSE),
and MIT licensed
[esbuild](https://github.com/evanw/esbuild/blob/v0.28.2/LICENSE.md) and
[is-number](https://github.com/jonschlinkert/is-number/blob/7.0.0/LICENSE).
[Bun's license notice](https://github.com/oven-sh/bun/blob/bun-v1.3.10/LICENSE.md)
distinguishes its MIT code from its bundled LGPL libraries. Node's release
[license file](https://github.com/nodejs/node/blob/v24.21.0/LICENSE) includes
the notices for its bundled libraries.

Bazel's [distribution guidance](https://bazel.build/rules/deploying) governs the
repository layout. In addition to Tomato, permissively licensed prior work
includes [parkrevil/rules_bun](https://github.com/parkrevil/rules_bun)
(Apache-2.0).
