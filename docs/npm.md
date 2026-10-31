# Per-package npm rules

The rules under [`//npm`](../npm) acquire a Bun workspace's registry packages
one package at a time. `bun.lock` is translated into a checked-in graph; each
locked release is fetched once, by its integrity, and becomes a unit of its own;
and a consumer's `node_modules` is linked from the units it reaches. A bump to
one package rebuilds that package's unit and the trees of the consumers that
reach it, and nothing else.

`bun_install` ([README](../README.md)) installs a whole workspace in one
repository rule instead. The two are independent; a module can use either.

## Setup

```starlark
# MODULE.bazel
bazel_dep(name = "curlewlabs_rules_bun", version = "<version>")
bazel_dep(name = "curlewlabs_rules_archive_store", version = "0.1.0")

# Neither module is in the Bazel Central Registry, and an override binds only
# the root module, so the root declares both: rules_bun's from its release
# notes, rules_archive_store's from its own.
archive_override(module_name = "curlewlabs_rules_bun", ...)
archive_override(module_name = "curlewlabs_rules_archive_store", ...)

npm = use_extension("@curlewlabs_rules_bun//npm:extensions.bzl", "npm")
npm.graph(
    graph = "//:npm_graph.json",
    exemptions = "//:npm_install_exemptions.json",
)
use_repo(npm, "npm")
```

```starlark
# BUILD.bazel
load("@curlewlabs_rules_bun//npm:defs.bzl", "npm_graph")

npm_graph(
    name = "npm_graph",
    bunfig = "bunfig.toml",
    lock = "bun.lock",
    out = "npm_graph.json",
)
```

```
# .bazelrc
common --@curlewlabs_rules_bun//npm:bun=//tools:bun
```

- **`//npm:bun`** names the Bun the rules run: the translator, the linked tree
  helper, and `npm_bun_install_test`. rules_bun acquires none. Name an
  executable target or a downloaded release's `bun` file, for the platform the
  action runs on; a `select()` behind an alias serves several. Until it is set,
  any action that runs Bun fails naming the flag.
- **`npm_graph`** writes the graph: `bazel run //:npm_graph` after any lock
  change, then commit the result. `//:npm_graph_test` fails while the
  checked-in graph differs from what the translator writes.
- **The extension** reads only the checked-in graph and exemptions, so the
  module lock records none of its repositories.

Start with an empty graph, `{"repositories": {}, "packages": {}, "workspaces":
{}, "hoisted": {}}`, so the extension can evaluate before the first
`bazel run`.

## Translation

[`npm_graph.ts`](../npm/npm_graph.ts) reads `bun.lock` with Bun's own JSONC
parser and writes the graph; its header owns the graph's fields. Only
default-registry packages are translated, and a lock holding any other source
fails translation.

A graph node is a release under the peers it links. Bun resolves a peer from the
packages that depend on it, not from where the lock installs it, so one release
can link different peers under different dependents; Bun's isolated store keeps
one directory for each, and the graph one node. A workspace's own copy of a
dependency, which the lock keys under the workspace's package name, is resolved
before the root's.

Bun's isolated store also hoists packages into a fallback directory,
`node_modules/.bun/node_modules`, which a package's own lookup reaches after its
edges, so a package that requires something it does not declare still finds it.
`bunfig.toml`'s `install.hoistPattern` must list the names hoisted there, as
literal package names, and the graph's `hoisted` names the node Bun links for
each. Bun's default, every name, is not decidable from the lock: an entry claims
the fallback under its dependency name but is linked under its package name, and
the links are written concurrently, so an `npm:` alias of another release races
the real name for one link. A literal list of names no alias shares has no such
race. Translation fails on a name no workspace installs at the top level.

The hub, `@npm` by default, declares one target per node, named by its id, and
one per workspace dependency at the path Node would find it, such as
`@npm//:node_modules/prettier` or `@npm//:packages/app/node_modules/eslint`. A
consumer names the latter, so a bump does not rename what it depends on. A
dependency on another workspace gets none; the graph records it as
`workspace:<directory>`, and `@npm//:workspaces.bzl` lists each workspace's
links and workspace dependencies. A dependency cycle cannot be a Bazel
dependency, so each member of one is an alias of a group target that reaches
every member. `npm_graph_test` checks, for the targets it names, that everything
a target reaches has its edges reached too. A target is analyzed only when
something depends on it, and analyzing one downloads its closure, so nothing
downloads a package no consumer reaches.

## Units

Building a node builds the units it reaches, for the configuration's platform:
macOS or Linux, on aarch64 or x86_64.

- **Per-file**, on every platform by default. A unit is a directory output at
  the node's store path, and each edge, root link and bin link is a
  `declare_symlink` artifact.
- **Archive**, on macOS with `--@curlewlabs_rules_bun//npm:macos_archives`. A
  unit is the release's tarball as a rules_archive_store `declared_archive`,
  shared by every node of the release, and a consumer's tree is built from the
  units by that module's helper. Set it only where the executor provides the
  archive store
  ([executor.md](https://github.com/curlewlabs-com/rules_archive_store/blob/main/docs/executor.md)).
  Without one the helper expands every unit into a private store in every action
  and reuses nothing.

On a worker that lays out each input file, a tree of many small files costs
every action a layout and a teardown per file; the archive representation turns
that into one input per unit and one published tree per worker instance.
Measure both representations on your own executor before choosing.

Each unit is read as npm reads a tarball, by stripping each member's first path
component whatever it is named, under rules_archive_store's tar member checks:
paths stay inside the package, links stay inside the tree, and names a
case-insensitive volume would merge are refused. A file the package's `bin`
names is made executable whatever mode the tarball gave it, and a named file
the tarball lacks fails the unit.

## Install steps

No lifecycle script runs, in any representation. A release's check reads its
tarball's own `package.json`, not the registry's manifest, which can disagree
with it, and fails on a `preinstall`, `install` or `postinstall` script, or a
`binding.gyp` at the package's root, that the exemptions file does not exempt.
Its failure prints each step's digest.

The exemptions file maps a package to each step it exempts, with that step's
`sha256` and a `reason` the step is unnecessary:

```json
{
  "esbuild": {
    "postinstall": {
      "sha256": "912d4d8f507b7b392ae422a459f98da94669a742e4cc43cfe061e630ee2846fe",
      "reason": "bin/esbuild finds the platform package's binary without it."
    }
  }
}
```

A script's digest is of its command as `package.json` holds it, not of the file
the command runs, so a release that keeps the command keeps its exemption, and
one that changes it fails until it is reviewed again. An entry for a package no
locked release has fails the hub, and `@npm//:install_exemptions` fails on a
step no locked release of its package holds with that digest; name it in an
`npm_graph_test`'s `checks` so a stale entry fails a test. A package that needs
its script's effect, such as a downloaded browser, is the consumer's to acquire
another way.

The check runs wherever a node is built, as an input of every unit rather than a
validation action, which Bazel would not run for an exec-configured tool.

## Linked trees

A consumer takes one tree for all of the workspace links it names, since a
package resolves another by name from the tree's root.

- **Edges** sit at `.store/<id>/node_modules/<dependency>` beside each unit,
  under the dependency's name, which an `npm:` alias makes differ from the
  package's. An edge to a node whose `os` or `cpu` excludes the platform is left
  out, as Bun skips that node.
- **The fallback.** The hoisted node is linked at
  `.store/node_modules/<package>`, which a unit's own lookup reaches after its
  edges.
- **Roots and bins.** A workspace link puts its dependency at
  `<workspace>/node_modules/<dependency>` and each of the package's bins at
  `<workspace>/node_modules/.bin/<bin>`. When two of a workspace's dependencies
  name one bin, as an `npm:` alias of a package the workspace also depends on
  does, the bin is the dependency's whose name sorts first, as Bun links it.
- **One repository.** Every per-file link is relative and stays inside the hub,
  so it resolves in an action's input root and in a test's runfiles alike.

Where units are archives, the store looks the tree up by what it holds: on a hit
the consumer mounts it with one link, and on a miss one helper process expands
the units the store lacks, builds the tree and publishes it. A bump to a package
the consumer does not reach changes nothing in its tree.

## Consumers

Load from [`@curlewlabs_rules_bun//npm:defs.bzl`](../npm/defs.bzl).

- **An action** that runs a tool from a tree names the workspace links as an
  exec-configured attribute, adds `NPM_TREE_ATTRS` to its rule's attributes,
  and prepares the action with `npm_tool_tree`, which takes the representation
  from the links. The command reads the tree's root, where `node_modules` sits
  for the root workspace, at the `path` it returns.
- **A test** takes its tree through `npm_tree_test`, which puts it at
  `$ARCHIVE_MOUNT_ROOT/tree` in either representation. A `bazel run` binary takes
  it through `npm_tree_binary` at the same path, in a root its launcher makes and
  removes once the binary exits. A test that also mounts archives takes its tree
  from rules_archive_store's `archive_test_launcher`, whose `tree` is
  `npm_launch_tree`'s.
- **A bin** started through its `#!/usr/bin/env node` line needs a Node on
  `PATH`, which an action does not have; run the bin's script under Bun
  (`bun --no-install node_modules/.bin/<bin>`) or a declared Node instead.

## What a layout must keep

- **A link never sits inside a directory output.** Under remote execution
  (observed with Buildbarn on macOS and Linux workers), a consumer received such
  a link as a copy, and a producing action holding a dangling link, or a
  relative link out of the tree, failed. A `declare_symlink` artifact reached
  the consumer as a link.
- **A unit is a real directory inside its linked tree,** never a link to an
  expansion elsewhere: Bun resolves a package's dependencies from the package's
  realpath, so a unit linked out of the tree finds none beside it.
- **A package's bins are executable,** whatever mode the tarball gave them.

## Testing the translation

The translator models how Bun links packages rather than letting Bun do it.
`npm_bun_install_test` is what notices a Bun release that links differently, or
a lock whose shape the model does not cover:

```starlark
load("@curlewlabs_rules_bun//npm:defs.bzl", "npm_bun_install_test")

npm_bun_install_test(
    name = "npm_bun_install_test",
    bunfig = "bunfig.toml",
    graph = "npm_graph.json",
    lock = "bun.lock",
    manifests = ["package.json", "packages/app/package.json"],
    tarballs = "@npm//:tarballs",
)
```

It installs the lock frozen with Bun's isolated linker and `--ignore-scripts`,
from a loopback registry serving only the hub's tarballs for the target
platform, so it reaches no network. It then compares Bun's store with the graph
from each side ([`npm_bun_install.py`](../npm/npm_bun_install.py)'s header owns
how a store directory matches a node), the workspaces' links and the fallback,
and each workspace's `.bin`. A failure names each unmatched store directory and
node, with Bun's links beside the graph's edges. Run it on every platform you
build for, and with each Bun you move to.

Two differences in `.bin` are expected and pass. Bun links some bins to a binary
the package's optional dependency ships, as esbuild's own postinstall would,
where the hub links the package's bin script, which finds the same binary. And
Bun links no bin of a workspace's own copy of a dependency, which the hub links
as npm does.
