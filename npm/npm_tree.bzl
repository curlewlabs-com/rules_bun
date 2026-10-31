"""A consumer's linked tree: the units and links its npm_link targets reach.

A consumer takes one tree for all of its npm_link targets, since a package
resolves another by name from the tree's root (remark loads remark-gfm from
the directory holding the `node_modules` remark-cli is in), so the tree holds
every link those targets add. Its units are the nodes they reach, each at its
store path, and its links are each node's edges, beside its unit, plus the
npm_links' own (docs/npm.md sec."Linked trees"). A node whose os or cpu
excludes the platform is left out, with every edge to it, as Bun skips it.

Per-file, the tree is the links and units the npm_link targets declare, laid
out in an action's input root or a program's runfiles. Where units are
archives, rules_archive_store's helper builds and publishes the tree
(archive_tree), keyed by what it holds, so a bump to a package the consumer
does not reach changes nothing here.
"""

load(
    "@curlewlabs_rules_archive_store//archive_store:archives.bzl",
    "archive_tree",
    "archive_tree_attrs",
    "archive_tree_run_launcher",
    "archive_tree_test_launcher",
)
load(":npm_package.bzl", "NpmLinkInfo", "NpmPackagesInfo", "npm_bin_files", "npm_hoisted_path", "npm_relative", "npm_store_path")

# What a rule whose actions read a tree adds to its attrs (npm_tool_tree). The
# helper's Bun is named by a label of its own: a test that also takes the
# target Bun, and names it in its args, would otherwise hold one label twice,
# which argument expansion refuses.
NPM_TREE_ATTRS = archive_tree_attrs(Label("//npm:tree_bun"))

# What a rule that launches a test or a `bazel run` binary with a tree adds
# (npm_launch_tree): the launched helper runs under the target
# configuration's Bun, and its manifest is still resolved by an action, under
# the exec one.
NPM_TREE_TEST_ATTRS = archive_tree_attrs(Label("//npm:tree_bun"), launched_bun = Label("//npm:bun"))

def npm_tree_layout(links):
    """The units and links of the tree `links` reach.

    Args:
      links: npm_link targets, configured where units are archives.

    Returns:
      A struct of `units`, a dict from a store path to the declared_archive
      cloned there, `links`, a dict from a path in the tree to its relative
      target, and `executables`, each unit's bin files, as archive_tree takes
      them.
    """
    if not _npm_links_archives(links):
        fail("npm_tree_layout builds trees of archive units; these links are configured for per-file units")
    records = depset(transitive = [link[NpmPackagesInfo].packages for link in links]).to_list()
    nodes = {record.node: record for record in records}
    units = {}
    tree_links = {}
    executables = []
    for record in records:
        if record.unit == None:
            continue
        if type(record.unit) != "Target":
            fail("npm_tree reads archive units, which are declared archives; %s's is %s" % (record.node, type(record.unit)))
        units[record.store_path] = record.unit
        if record.hoisted:
            at = npm_hoisted_path(record.package)
            tree_links[at] = npm_relative(at, record.store_path)
        executables += [record.store_path + "/" + path for path in npm_bin_files(record.bin)]
        for dependency, node in record.edges.items():
            if node not in nodes:
                fail("%s's edge %s names %s, which no npm_link here reaches" % (record.node, dependency, node))
            if nodes[node].unit == None:
                continue
            at = npm_store_path(record.node, dependency)
            tree_links[at] = npm_relative(at, nodes[node].store_path)
    for link in links:
        for at, target in link[NpmLinkInfo].links.items():
            if tree_links.get(at, target) != target:
                fail("%s links %s to %s, where another link names %s" % (link.label, at, target, tree_links[at]))
            tree_links[at] = target
    return struct(units = units, links = tree_links, executables = executables)

def npm_tree(ctx, name, links, writable = False):
    """Prepare an action that reads the linked tree of `links` (archive_tree's arguments and result).

    Args:
      ctx: the rule context; the rule's attrs include NPM_TREE_ATTRS.
      name: distinguishes this action's tree among the target's actions.
      links: npm_link targets, configured where units are archives.
      writable: give the action a writable clone of the tree.

    Returns:
      archive_tree's struct; the command reads the tree's root at `path`.
    """
    layout = npm_tree_layout(links)
    return archive_tree(ctx, name, layout.units, layout.links, writable = writable, executables = layout.executables)

def npm_tool_tree(ctx, name, links, command):
    """Prepare an action that runs `command` with the linked tree of `links`, in either representation.

    Where units are archives the tree is the one the archive store publishes
    (npm_tree), and the action runs its helper, which mounts the tree and then
    runs `command`. Per-file, the tree is the links and units the npm_link
    targets declare, laid out in the action's input root, and the action runs
    `command` itself. Either way `path` is the execution-root path of the
    tree's root, where `node_modules` sits for the root workspace and
    `<workspace>/` for another.

    Args:
      ctx: the rule context; the rule's attrs include NPM_TREE_ATTRS.
      name: distinguishes this action's tree among the target's actions.
      links: npm_link targets, configured for the platform the action runs on.
      command: FilesToRunProvider of the tool the action runs.

    Returns:
      A struct whose `executable` and leading `arguments` the action runs,
      followed by the command's own arguments, whose `inputs` and `tools` it
      declares, and whose `path` is the tree's root.
    """
    if _npm_links_archives(links):
        tree = npm_tree(ctx, name, links)
        return struct(
            executable = tree.executable,
            arguments = tree.arguments + [command.executable.path],
            inputs = tree.inputs,
            tools = tree.tools + [command],
            path = tree.path,
        )

    return struct(
        executable = command,
        arguments = [],
        inputs = depset(transitive = [link[DefaultInfo].files for link in links]),
        tools = [],
        path = _npm_per_file_root(links, lambda file: file.path),
    )

def npm_launch_tree(links):
    """The linked tree of `links` for a test launcher that mounts archives too.

    Args:
      links: npm_link targets, configured as the test is.

    Returns:
      A struct whose `tree` is archive_test_launcher's `tree` and whose `data`
      are the targets the test carries for it: per-file, the links themselves,
      whose files are the tree.
    """
    if _npm_links_archives(links):
        layout = npm_tree_layout(links)
        return struct(
            tree = struct(units = layout.units, links = layout.links, executables = layout.executables),
            data = [],
        )
    return struct(
        tree = struct(directory = _npm_per_file_root(links, lambda file: file.short_path)),
        data = links,
    )

def _npm_links_archives(links):
    """Whether `links`, all configured for one platform, make a tree of archive units."""
    platforms = {(link[NpmLinkInfo].platform, link[NpmLinkInfo].archives): True for link in links}
    if len(platforms) != 1:
        fail("these npm links are configured for %s; a tree is one platform's, in one representation" % sorted(platforms))
    return platforms.keys()[0][1]

def _npm_per_file_root(links, path):
    """Where the per-file links' tree has its root, by `path` of their files."""
    linked = [link[NpmLinkInfo] for link in links if link[NpmLinkInfo].link]
    if not linked:
        fail("these npm links are all excluded on this platform, so no tree holds them")
    roots = {}
    for info in linked:
        at = path(info.link)
        if not at.endswith("/" + info.path):
            fail("%s does not sit at %s from its tree's root" % (at, info.path))
        roots[at.removesuffix("/" + info.path)] = True
    if len(roots) != 1:
        fail("these npm links sit in several trees: %s" % sorted(roots))
    return roots.keys()[0]

# How a per-file launcher, where no helper mounts the tree, gives the launched
# program `$ARCHIVE_MOUNT_ROOT`: a test's under its own temporary directory, a
# `bazel run` binary's in one the launcher makes and removes.
_PER_FILE_TEST_ROOT = """ARCHIVE_TEST_STARTED=$(date +%s)
export ARCHIVE_TEST_STARTED
ARCHIVE_MOUNT_ROOT="${TEST_TMPDIR:?}/npm-tree"
mkdir -p "$ARCHIVE_MOUNT_ROOT"
"""

_PER_FILE_RUN_ROOT = """ARCHIVE_MOUNT_ROOT=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/npm-tree.XXXXXX")
trap '/bin/rm -rf "$ARCHIVE_MOUNT_ROOT"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
"""

def _npm_tree_launch(ctx, target, run):
    """`target` launched with the tree of `ctx.attr.links`, as a test or, with `run`, a binary."""
    links = ctx.attr.links
    if _npm_links_archives(links):
        layout = npm_tree_layout(links)
        launcher = archive_tree_run_launcher if run else archive_tree_test_launcher
        launch = launcher(ctx, layout.units, layout.links, target, ctx.attr.data, ctx.attr.env, executables = layout.executables)
        return [
            DefaultInfo(executable = launch.executable, runfiles = launch.runfiles),
            RunEnvironmentInfo(environment = launch.environment),
        ]

    # Per-file, the links and units are the program's runfiles, so the
    # launcher names their root where the archive helper would have mounted
    # the tree, and a program reads `$ARCHIVE_MOUNT_ROOT/tree` in either
    # representation.
    root = _npm_per_file_root(links, lambda file: file.short_path)
    executable = target[DefaultInfo].files_to_run.executable
    for path in [root, executable.short_path]:
        # The launcher quotes each path in single quotes.
        if "'" in path:
            fail("an npm tree launcher cannot launch a path holding a single quote: " + path)
    launcher = ctx.actions.declare_file(ctx.label.name + ".npm_tree_launcher.sh")

    # A binary's launcher stays its parent, so its trap removes the root.
    ctx.actions.write(launcher, """#!/bin/sh
set -eu
{root_setup}ln -s "$PWD/{root}" "$ARCHIVE_MOUNT_ROOT/tree"
export ARCHIVE_MOUNT_ROOT
{launch}'{executable}' "$@"
""".format(
        root_setup = _PER_FILE_RUN_ROOT if run else _PER_FILE_TEST_ROOT,
        root = root,
        launch = "" if run else "exec ",
        executable = executable.short_path,
    ), is_executable = True)
    runfiles = ctx.runfiles(files = [executable]).merge(target[DefaultInfo].default_runfiles)
    for carried in links + ctx.attr.data:
        runfiles = runfiles.merge(ctx.runfiles(transitive_files = carried[DefaultInfo].files)).merge(carried[DefaultInfo].default_runfiles)
    return [
        DefaultInfo(executable = launcher, runfiles = runfiles),
        RunEnvironmentInfo(environment = {name: ctx.expand_location(value, ctx.attr.data) for name, value in ctx.attr.env.items()}),
    ]

def _npm_tree_test_impl(ctx):
    return _npm_tree_launch(ctx, ctx.attr.test, run = False)

def _npm_tree_binary_impl(ctx):
    return _npm_tree_launch(ctx, ctx.attr.binary, run = True)

npm_tree_test = rule(
    implementation = _npm_tree_test_impl,
    doc = "Runs `test` with the linked tree of `links` at `$ARCHIVE_MOUNT_ROOT/tree`: the archive store's published tree where units are archives, otherwise the links and units in its runfiles.",
    attrs = {
        "links": attr.label_list(mandatory = True, providers = [NpmLinkInfo, NpmPackagesInfo]),
        "test": attr.label(executable = True, cfg = "target", mandatory = True),
        "data": attr.label_list(allow_files = True, doc = "What `args` and `env` name."),
        "env": attr.string_dict(doc = "Environment for the test, with `$(rootpath)` of `data` expanded."),
    } | NPM_TREE_TEST_ATTRS,
    test = True,
)

npm_tree_binary = rule(
    implementation = _npm_tree_binary_impl,
    doc = "Runs `binary` under `bazel run` with the linked tree of `links` at `$ARCHIVE_MOUNT_ROOT/tree`, as npm_tree_test runs a test.",
    attrs = {
        "links": attr.label_list(mandatory = True, providers = [NpmLinkInfo, NpmPackagesInfo]),
        "binary": attr.label(executable = True, cfg = "target", mandatory = True),
        "data": attr.label_list(allow_files = True, doc = "What `args` and `env` name."),
        "env": attr.string_dict(doc = "Environment for the binary, with `$(rootpath)` of `data` expanded."),
    } | NPM_TREE_TEST_ATTRS,
    executable = True,
)
