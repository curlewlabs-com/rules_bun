# Frozen Bun workspaces for [Bazel](https://bazel.build)

A repository rule for acquiring a Bun workspace before build actions run. Bazel
supplies verified Bun and Node binaries; Bun supplies frozen lockfile resolution
and platform package selection. The custom rule supplies the input layout and
exports the resulting files and symlinks.

This module is an acquisition prototype. It is not published in the Bazel
Central Registry. It does not provide build/test rules or an execution sandbox.

## Versions

The current ruleset version is **0.4.0**. The current development toolchain is
**Bazel 9.2.0**, **Bun 1.3.10**, and **Node 24.21.0**. The pins live in
[.bazelversion](.bazelversion), [tests/deps.bzl](tests/deps.bzl), and
[MODULE.bazel](MODULE.bazel). These are the current selections, not a broader
compatibility guarantee. The [compatibility policy](docs/compatibility.md)
defines the supported baseline, public API, lifecycle/registry boundaries, and
versioning commitments.

## Related project

[Tomato's rules_bun](https://github.com/tomato-bazel/rules_bun) is a useful
MIT-licensed option providing frozen Bun installation, Bun toolchains, and
execution rules. This project is an independent implementation focused on
acquiring selected workspace dependencies with caller-owned Bun and Node
runtimes, then exporting the installed layout for a consumer to restore.
See [Consume](#consume) for that boundary.

## Use

Start with the [independent consumer](examples/consumer/README.md) for a
runnable module using a verified source archive and consumer-owned runtimes.
Source archives, checksums, and installation snippets are published on the
[releases page](https://github.com/curlewlabs-com/rules_bun/releases).

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

Set `reproducible = True` to let Bazel reuse an acquisition from its repo
contents cache in other workspaces and output bases instead of installing again.
It declares the repository a function of its declared inputs alone, so leave it
off when a lifecycle script writes the repository's absolute path, the time, or
other host state into its output. Either way, the rule removes its private
runtime links and package cache once the closure is recorded.

Bun's isolated linker leaves two parts of its layout to install timing, and the
rule fixes both so that the same inputs produce the same closure. The fallback
link for a package name in `node_modules/.bun/node_modules` is claimed per
declared dependency name but written at the package's own name, so an alias
such as `string-width-cjs` (`npm:string-width@^4.2.0`) races a dependency on
`string-width` itself. The rule keeps aliases out of that directory through
Bun's `hoist-pattern`, which leaves each link to the version Bun chooses for the
package's own name. Bun also skips a dependency's bin when the dependency is in
a cycle with the package linking it and is not installed yet; the rule adds
each such link as Bun writes it when the dependency comes first.

Declare every root workspace manifest even when selecting only one consumer.
Workspace paths must be explicit directories in the root manifest: workspace
globs are deliberately unsupported. `inputs` also accepts text installation
configuration, patches, and source files needed by trusted lifecycle scripts,
with root-relative destinations. A root `.npmrc` or `bunfig.toml` that sets a
hoist pattern is refused, since the rule sets Bun's own. Bun reads the original manifests and committed
lock; the rule does not translate either format. Runtime execution must include
separately declared workspace sources when packages link to local code.

Package bytes come from Bazel's downloader rather than from Bun. The declared
Bun first reads the committed lock with its own parser and lists the
default-registry tarballs the acquisition host can install. Bazel downloads each
one against its locked integrity and keeps it in the repository cache, so later
acquisitions on that host reuse the verified file instead of downloading it
again. Bun then installs from a loopback registry that serves exactly those
tarballs. Proxied traffic during installation is refused, and any request
outside the listed tarballs fails acquisition. Lock entries from other sources,
such as tarball URLs, Git, or another registry, are rejected.

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
Bazel's downloader is acquisition's intended network path; a lifecycle program
that ignores proxy settings is not contained. Consumer actions must disable
auto-install and supply their own qualified runtime, network policy, and
execution identity. The
content manifest is not a complete execution-platform identity, and this rule
alone does not establish hermeticity or qualify reusable action results.

## Verify

Use the Bazel release selected by `.bazelversion` and Python from your
development environment:

```sh
python3 tests/check.py --bazel /path/to/bazel
python3 tests/check_consumer.py --bazel /path/to/bazel
python3 tests/check_state.py --bazel /path/to/bazel
```

The integration check acquires real packages, restores their exported payload,
resolves a workspace dependency, and runs native esbuild with both runtimes. It
verifies that a workspace-only consumer cannot resolve root tooling, that
removing the native executable causes failure, and that acquisition rejects a
manifest the lock does not satisfy, an omitted workspace manifest, a tarball
whose bytes disagree with its locked integrity, and a lock entry from a source
other than the default registry. Acquisition needs network access on a cold
repository cache.

The archive check exercises an independent module with development dependencies
disabled, lifecycle output, native execution after removing acquisition state,
and corrupt/missing payload rejection. It packages committed `HEAD`; commit
candidate changes before using it as evidence. See the consumer README for
testing a prepared source archive.

The [integration workflow](.github/workflows/ci.yaml) runs these checks
on Linux and macOS, on x64 and arm64. Its
[run history](https://github.com/curlewlabs-com/rules_bun/actions/workflows/ci.yaml)
records the tested revision and platform results. A platform selection is not a
promise about other OS releases or runtime versions.

The state check rebuilds without cleaning after changing lifecycle inputs,
installer bytes, and real runtime binaries at unchanged paths. It also overlaps
independent acquisitions at a lifecycle barrier and verifies their private
state and exported native payloads. A reproducible acquisition must be reused
by a second workspace sharing the repository cache without running its
lifecycle, and must run again once an input changes. These checks exercise the pinned test
runtimes; they do not establish a broader version compatibility policy.
Remaining readiness work is tracked in
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
