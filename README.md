# Frozen Bun workspaces for [Bazel](https://bazel.build)

A repository rule for acquiring a Bun workspace before build actions run. Bazel
supplies verified Bun and Node binaries; Bun supplies frozen lockfile resolution
and platform package selection. The custom rule supplies the input layout and
exports the resulting files and symlinks.

This module is an acquisition prototype. It is not published in the Bazel
Central Registry. It does not provide build/test rules or an execution sandbox.

## Use

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
```

The integration check acquires real packages, restores their exported payload,
resolves a workspace dependency, and runs native esbuild with both runtimes. It
verifies that a workspace-only consumer cannot resolve root tooling, that
removing the native executable causes failure, and that acquisition rejects a
changed manifest against a frozen lock or an omitted workspace manifest. The
local evidence covers macOS arm64; Linux execution remains unverified.
Acquisition needs network access on a cold cache.

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
repository layout. Permissively licensed alternatives include
[tomato-bazel/rules_bun](https://github.com/tomato-bazel/rules_bun) (MIT) and
[parkrevil/rules_bun](https://github.com/parkrevil/rules_bun) (Apache-2.0). This
module focuses on acquisition using caller-supplied runtimes and explicit
workspace inputs.
