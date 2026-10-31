"""The npm dependency graph as targets.

The npm module extension generates these into its hub from the graph npm_graph
writes (docs/npm.md): one npm_release per locked release, one npm_package per
graph node, one npm_install_exemption per package the exemptions file names,
plus an npm_link for each registry dependency of each workspace. A target's
NpmPackagesInfo carries the record of every package it reaches, its own
included, so a consumer takes the units and edges it needs from its
dependencies instead of walking the lock.

A node's unit is its release's tarball in one of two representations
(docs/npm.md sec."Units"):

- **Per-file**, on every platform by default: a directory output per node, at
  the node's store path, since a unit must be a real directory inside its
  linked tree and a directory output has one path. Its edges are
  declare_symlink artifacts beside it, and an npm_link adds its workspace's
  `node_modules/<dependency>` link and the package's `.bin` links. Every link
  is relative and stays inside the hub, so it resolves wherever the hub's
  outputs are laid out, in an action's input root and in a test's runfiles
  alike. A link inside a directory output would reach a consumer as a copy,
  and Bun resolves a package's dependencies from its realpath.
- **Archive**, on macOS when `//npm:macos_archives` is set: the release's
  declared_archive, which every node of the release shares, converted from the
  copy of the tarball the release's check writes once it passes, so reading
  the unit in any configuration runs the check. Neither a node nor an npm_link
  adds a link: rules_archive_store's helper builds and publishes a consumer's
  tree from the units and links npm_tree.bzl names, which needs an executor
  that provides the store.

A node whose os or cpu excludes the platform has no unit.
"""

load("@curlewlabs_rules_archive_store//archive_store:archives.bzl", "ArchiveInfo")
load(":flags.bzl", "NpmFlagInfo")
load(":platforms.bzl", "NPM_PLATFORMS", "npm_allows")

NpmPackageRecordInfo = provider(
    doc = "One graph node: a locked release, under the peers it links there.",
    fields = {
        "node": "Its id in npm_graph.generated.json, which names its target in @npm.",
        "package": "Its npm name.",
        "version": "Its locked version.",
        "archive": "Its registry tarball, verified against the locked integrity.",
        "os": "npm's os constraint, empty when unconstrained.",
        "cpu": "npm's cpu constraint, empty when unconstrained.",
        "bin": "Bin name to its path inside the package.",
        "edges": "Dependency name, peers included, to the node it links.",
        "hoisted": "Whether it is the node Bun's store hoists under its name, which a linked tree then links at npm_hoisted_path: the fallback a package's own lookup reaches after its edges.",
        "store_path": "Where its unit sits in a linked tree: npm_store_path of its node and package.",
        "unit": "Its unit: per-file, the directory output at store_path; archive, its release's declared_archive target, which npm_tree.bzl gives archive_tree as a unit of a consumer's linked tree; None when its os or cpu excludes the platform.",
        "checked": "File: its release's install-step check. The per-file unpack takes it as an input, and the archive unit is converted from the tarball the check passed, so reading a unit runs the check in every configuration.",
    },
)

NpmReleaseInfo = provider(
    doc = "One locked release, which each of its graph nodes shares.",
    fields = {
        "package": "Its npm name.",
        "version": "Its locked version.",
        "archive": "File: its registry tarball, verified against the locked integrity.",
        "checked": "File: the install steps the release's check found, each `<step> <digest>`, all of them exempted.",
        "passed": "File: the tarball as the check passed it, the release's default output, which its declared_archive converts.",
    },
)

_PLATFORM_ATTRS = {
    "_os_" + os: attr.label(default = "@platforms//os:" + os)
    for os in ["macos", "linux"]
} | {
    "_cpu_" + cpu: attr.label(default = "@platforms//cpu:" + cpu)
    for cpu in ["aarch64", "x86_64"]
} | {
    "_macos_archives": attr.label(default = Label("//npm:macos_archives"), providers = [NpmFlagInfo]),
}

def npm_store_path(node, package):
    """A node's directory in a linked tree: its id with `/` written `+`, as Bun's isolated store writes a scoped package's."""
    return ".store/%s/node_modules/%s" % (node.replace("/", "+"), package)

def npm_bin_files(bin):
    """The files a package's `bin` names, as normalized paths inside the package.

    A unit marks each executable whatever mode the tarball gave it, so a tool
    started by path runs (docs/npm.md sec."Units").

    Args:
      bin: a package's bin, name to its path inside the package.

    Returns:
      The paths, sorted, each without a leading `./`.
    """
    files = {}
    for name, target in bin.items():
        path = target.removeprefix("./")
        if path.startswith("/") or any([part in ("", ".", "..") for part in path.split("/")]):
            fail("bin %s -> %r is not a path inside its package" % (name, target))
        files[path] = True
    return sorted(files)

def npm_hoisted_path(package):
    """Where a linked tree links a hoisted node: a package's own lookup reaches it after its edges, as it reaches Bun's `.bun/node_modules`."""
    return ".store/node_modules/" + package

def npm_relative(link, target):
    """The target_path of a link to `target`.

    Args:
      link: the link's path, relative to a root: the package, or a linked
        tree's root.
      target: what it links to, relative to the same root.

    Returns:
      The link's target, relative to the link's own directory.
    """
    start = link.split("/")[:-1]
    parts = target.split("/")
    common = 0
    for here, there in zip(start, parts):
        if here != there:
            break
        common += 1
    return "/".join([".."] * (len(start) - common) + parts[common:])

def _declare_link(ctx, path, target_path):
    link = ctx.actions.declare_symlink(path)
    ctx.actions.symlink(output = link, target_path = target_path)
    return link

def _link(ctx, path, target):
    return _declare_link(ctx, path, npm_relative(path, target))

def _npm_platform(ctx):
    """npm's names for the target platform, and whether its units are archives."""
    for platform in NPM_PLATFORMS:
        os, cpu = platform.constraints
        if (ctx.target_platform_has_constraint(getattr(ctx.attr, "_os_" + os)[platform_common.ConstraintValueInfo]) and
            ctx.target_platform_has_constraint(getattr(ctx.attr, "_cpu_" + cpu)[platform_common.ConstraintValueInfo])):
            return struct(
                os = platform.os,
                cpu = platform.cpu,
                archives = platform.os == "darwin" and ctx.attr._macos_archives[NpmFlagInfo].value,
            )
    fail("npm units are built for macOS and Linux, on aarch64 and x86_64.")

def _npm_release_impl(ctx):
    checked = ctx.actions.declare_file(ctx.label.name + ".install_steps")

    # The archive's own name, from which the conversion reads its format.
    passed = ctx.actions.declare_file(ctx.label.name + "/" + ctx.file.archive.basename)
    arguments = ctx.actions.args()
    arguments.add("check")
    arguments.add("--tarball", ctx.file.archive)
    arguments.add("--package", ctx.attr.package)
    for step, digest in sorted(ctx.attr.exempt.items()):
        arguments.add("--exempt", "%s=%s" % (step, digest))
    arguments.add("--stamp", checked)
    arguments.add("--passed", passed)
    ctx.actions.run(
        executable = ctx.executable._npm_tarball,
        arguments = [arguments],
        inputs = [ctx.file.archive],
        outputs = [checked, passed],
        mnemonic = "NpmInstallCheck",
        progress_message = "Checking %{label}'s install steps",
    )
    return [
        NpmReleaseInfo(
            package = ctx.attr.package,
            version = ctx.attr.version,
            archive = ctx.file.archive,
            checked = checked,
            passed = passed,
        ),
        DefaultInfo(files = depset([passed])),
    ]

npm_release = rule(
    implementation = _npm_release_impl,
    doc = "A locked release, whose install steps are checked once for all of its nodes. Its default output is the tarball as the check passed it.",
    attrs = {
        "package": attr.string(mandatory = True),
        "version": attr.string(mandatory = True),
        "archive": attr.label(mandatory = True, allow_single_file = True),
        "exempt": attr.string_dict(doc = "Each install step the exemptions file exempts for the package, to its digest."),
        "_npm_tarball": attr.label(
            default = Label("//npm:npm_tarball"),
            executable = True,
            cfg = "exec",
        ),
    },
    provides = [NpmReleaseInfo],
)

def _npm_install_exemption_impl(ctx):
    audited = ctx.actions.declare_file(ctx.label.name + ".audited")
    arguments = ctx.actions.args()
    arguments.add("exemptions")
    arguments.add("--package", ctx.attr.package)
    for step, digest in sorted(ctx.attr.exempt.items()):
        arguments.add("--exempt", "%s=%s" % (step, digest))
    checked = [release[NpmReleaseInfo].checked for release in ctx.attr.releases]
    for stamp in checked:
        arguments.add("--checked", stamp)
    arguments.add("--stamp", audited)
    ctx.actions.run(
        executable = ctx.executable._npm_tarball,
        arguments = [arguments],
        inputs = checked,
        outputs = [audited],
        mnemonic = "NpmInstallExemptions",
        progress_message = "Checking %{label} still matches a locked release",
    )
    return [DefaultInfo(files = depset([audited]))]

npm_install_exemption = rule(
    implementation = _npm_install_exemption_impl,
    doc = "Fails when an exemption of the package matches no install step a check of its locked releases found.",
    attrs = {
        "package": attr.string(mandatory = True),
        "exempt": attr.string_dict(mandatory = True),
        "releases": attr.label_list(mandatory = True, providers = [NpmReleaseInfo]),
        "_npm_tarball": attr.label(
            default = Label("//npm:npm_tarball"),
            executable = True,
            cfg = "exec",
        ),
    },
)

NpmPackagesInfo = provider(
    doc = "The packages a graph target reaches.",
    fields = {
        "packages": "depset of NpmPackageRecordInfo, this target's own first.",
    },
)

def _npm_platform_allows(platform, os, cpu):
    return npm_allows(os, platform.os) and npm_allows(cpu, platform.cpu)

def _unit(ctx, platform, release, store_path):
    if not _npm_platform_allows(platform, ctx.attr.os, ctx.attr.cpu):
        return None
    if platform.archives:
        return ctx.attr.declared
    directory = ctx.actions.declare_directory(store_path)
    ctx.actions.run(
        executable = ctx.executable._npm_tarball,
        arguments = ["unpack", "--tarball", release.archive.path, "--into", directory.path] +
                    ["--executable=" + path for path in npm_bin_files(ctx.attr.bin)],
        # The check as an input, not a validation: Bazel runs no validation
        # of an exec-configured target, and a tool's packages are one. Its
        # bytes follow from the tarball's, so the unit's key still moves only
        # with the tarball.
        inputs = [release.archive, release.checked],
        outputs = [directory],
        mnemonic = "NpmUnpack",
        progress_message = "Unpacking %{label}",
    )
    return directory

def _npm_package_impl(ctx):
    release = ctx.attr.release[NpmReleaseInfo]
    store_path = npm_store_path(ctx.attr.node, release.package)
    platform = _npm_platform(ctx)
    unit = _unit(ctx, platform, release, store_path)
    record = NpmPackageRecordInfo(
        node = ctx.attr.node,
        package = release.package,
        version = release.version,
        archive = release.archive,
        os = ctx.attr.os,
        cpu = ctx.attr.cpu,
        bin = ctx.attr.bin,
        edges = ctx.attr.edges,
        hoisted = ctx.attr.hoisted,
        store_path = store_path,
        unit = unit,
        checked = release.checked,
    )
    own = [depset([release.checked])]
    if type(unit) == "Target":
        own.append(unit[DefaultInfo].files)
    elif unit != None:
        # Bun skips a package its os or cpu excludes, so no edge names one.
        edges = [
            _link(
                ctx,
                npm_store_path(ctx.attr.node, dependency),
                npm_store_path(node, ctx.attr.aliases.get(dependency, dependency)),
            )
            for dependency, node in sorted(ctx.attr.edges.items())
            if _npm_platform_allows(platform, ctx.attr.edge_os.get(dependency, []), ctx.attr.edge_cpu.get(dependency, []))
        ]
        if ctx.attr.hoisted:
            edges.append(_link(ctx, npm_hoisted_path(release.package), store_path))
        own.append(depset([unit] + edges))
    return [
        NpmPackagesInfo(packages = depset(
            [record],
            transitive = [dep[NpmPackagesInfo].packages for dep in ctx.attr.deps],
            order = "preorder",
        )),
        # Every unit and check the target reaches, so building it in any
        # configuration builds them.
        DefaultInfo(files = depset(transitive = own + [dep[DefaultInfo].files for dep in ctx.attr.deps])),
    ]

npm_package = rule(
    implementation = _npm_package_impl,
    doc = "A graph node; deps are the targets its edges name, except edges back into its own cycle.",
    attrs = {
        "node": attr.string(mandatory = True),
        "release": attr.label(mandatory = True, providers = [NpmReleaseInfo]),
        "declared": attr.label(mandatory = True, providers = [ArchiveInfo], doc = "Its release's tarball as a declared_archive, converted from the copy the release's check passed: its unit where units are archives."),
        "os": attr.string_list(),
        "cpu": attr.string_list(),
        "bin": attr.string_dict(),
        "edges": attr.string_dict(),
        "hoisted": attr.bool(doc = "Whether Bun's store hoists this node under its package's name."),
        "aliases": attr.string_dict(doc = "Dependency name to the package its edge links, for an edge whose name is not the package's own (an `npm:` alias)."),
        "edge_os": attr.string_list_dict(doc = "Dependency name to the os constraint of the node its edge links, for each constrained one."),
        "edge_cpu": attr.string_list_dict(doc = "Dependency name to the cpu constraint of the node its edge links, for each constrained one."),
        "deps": attr.label_list(providers = [NpmPackagesInfo]),
        "_npm_tarball": attr.label(
            default = Label("//npm:npm_tarball"),
            executable = True,
            cfg = "exec",
        ),
    } | _PLATFORM_ATTRS,
    provides = [NpmPackagesInfo],
)

NpmLinkInfo = provider(
    doc = "A workspace's links to one of its registry dependencies.",
    fields = {
        "links": "Each link it adds to a linked tree, by its path from the tree's root to its relative target: `<workspace>/node_modules/<dependency>` and each `<workspace>/node_modules/.bin/<bin>`. Empty where the package's os or cpu excludes the platform.",
        "path": "`<workspace>/node_modules/<dependency>`: where link sits from the tree's root.",
        "platform": "npm's `<os>/<cpu>` for the platform it was configured for, such as `darwin/arm64`: one tree holds one platform's links, whether or not the package is linked there.",
        "archives": "Whether its tree is built from archive units by the archive store, rather than laid out from per-file units and links.",
        "link": "File: `<workspace>/node_modules/<dependency>` as the per-file representation declares it, so the directory holding its `node_modules` is the root Node resolves the dependency from; None where units are archives, whose trees the archive store builds, and wherever links is empty.",
        "bins": "Bin name to its File as the per-file representation declares it, `<workspace>/node_modules/.bin/<bin>`; empty wherever link is None.",
    },
)

def _npm_link_impl(ctx):
    package = ctx.attr.package
    platform = _npm_platform(ctx)
    files = [package[DefaultInfo].files]
    modules = "node_modules" if ctx.attr.workspace == "." else ctx.attr.workspace + "/node_modules"
    path = modules + "/" + ctx.attr.dependency
    links = {}
    bin_paths = {}
    if _npm_platform_allows(platform, ctx.attr.os, ctx.attr.cpu):
        links[path] = npm_relative(path, npm_store_path(ctx.attr.node, ctx.attr.package_name))
        for name, target in sorted(ctx.attr.bin.items()):
            bin_paths[name] = modules + "/.bin/" + name
            links[bin_paths[name]] = npm_relative(bin_paths[name], path + "/" + target.removeprefix("./"))
    link = None
    bins = {}
    if not platform.archives and links:
        link = _declare_link(ctx, path, links[path])
        bins = {name: _declare_link(ctx, at, links[at]) for name, at in bin_paths.items()}
        files.append(depset([link] + bins.values()))
    return [
        package[NpmPackagesInfo],
        NpmLinkInfo(links = links, path = path, platform = platform.os + "/" + platform.cpu, archives = platform.archives, link = link, bins = bins),
        DefaultInfo(files = depset(transitive = files)),
        OutputGroupInfo(link = depset([link] if link else [])),
    ]

npm_link = rule(
    implementation = _npm_link_impl,
    doc = """A workspace's registry dependency at the path Node finds it, such as @npm//:node_modules/prettier.

    It reaches what its package reaches. Where units are per-file it also
    links the package at `<workspace>/node_modules/<dependency>` and its bins in
    `.bin`, so an action or test that takes it resolves the package, and the
    packages that package reaches, from that workspace's root. Its `link`
    output group is the package link alone, for a `$(rootpath)`.
    """,
    attrs = {
        "workspace": attr.string(mandatory = True, doc = "The workspace's directory, `.` for the root."),
        "dependency": attr.string(mandatory = True),
        "package": attr.label(mandatory = True, providers = [NpmPackagesInfo], doc = "The node it links, or the cycle group that reaches it."),
        "node": attr.string(mandatory = True),
        "package_name": attr.string(mandatory = True, doc = "The node's npm name."),
        "os": attr.string_list(),
        "cpu": attr.string_list(),
        "bin": attr.string_dict(doc = "The package's bins the workspace links under `.bin`: those no dependency whose name sorts first also names."),
    } | _PLATFORM_ATTRS,
    provides = [NpmPackagesInfo, NpmLinkInfo],
)

def _npm_package_cycle_impl(ctx):
    return [
        NpmPackagesInfo(packages = depset(
            transitive = [member[NpmPackagesInfo].packages for member in ctx.attr.members],
            order = "preorder",
        )),
        DefaultInfo(files = depset(transitive = [member[DefaultInfo].files for member in ctx.attr.members])),
    ]

npm_package_cycle = rule(
    implementation = _npm_package_cycle_impl,
    doc = "The members of one dependency cycle, which each member's public target names, so reaching any member reaches them all.",
    attrs = {
        "members": attr.label_list(mandatory = True, providers = [NpmPackagesInfo]),
    },
    provides = [NpmPackagesInfo],
)

NpmTarballsInfo = provider(
    doc = "The registry tarballs Bun downloads on one platform.",
    fields = {"tarballs": "Each release's repository name to its tarball File."},
)

def _npm_tarballs_impl(ctx):
    tarballs = {}
    for target, name in ctx.attr.tarballs.items():
        files = target[DefaultInfo].files.to_list()
        if len(files) != 1:
            fail("%s holds %d files; a release's repository holds its tarball" % (target.label, len(files)))
        tarballs[name] = files[0]
    return [
        NpmTarballsInfo(tarballs = tarballs),
        DefaultInfo(files = depset(tarballs.values())),
    ]

npm_tarballs = rule(
    implementation = _npm_tarballs_impl,
    doc = "The hub's tarballs for one platform, by repository name, which npm_bun_install_test serves Bun.",
    attrs = {
        "tarballs": attr.label_keyed_string_dict(allow_files = True, doc = "Each release's tarball to its repository name."),
    },
    provides = [NpmTarballsInfo],
)

def _npm_graph_test_impl(ctx):
    unresolved = []
    for target in ctx.attr.packages:
        records = target[NpmPackagesInfo].packages.to_list()
        reached = {record.node: True for record in records}
        for record in records:
            for dependency, node in record.edges.items():
                if node not in reached:
                    unresolved.append("%s: %s -> %s (%s)" % (target.label, record.node, dependency, node))
    if unresolved:
        fail("Graph edges name packages their targets do not reach:\n  " + "\n  ".join(sorted(unresolved)))
    executable = ctx.actions.declare_file(ctx.label.name)
    ctx.actions.write(executable, "#!/bin/sh\nexit 0\n", is_executable = True)

    # Built, not laid out: the test reads none of them, and a spawn that took
    # per-file units would lay out every file of them. Building the test
    # builds this target's validation outputs, exec-configured dependencies'
    # files included.
    built = depset(transitive = [target[DefaultInfo].files for target in ctx.attr.packages + ctx.attr.exec_packages + ctx.attr.checks])
    return [
        DefaultInfo(executable = executable),
        OutputGroupInfo(_validation = built),
    ]

npm_graph_test = rule(
    implementation = _npm_graph_test_impl,
    doc = """Fails analysis when a target reaches a node whose edges name a node it does not reach.

    A target's deps are its edges with cycle edges removed, so this is what
    proves the cycle grouping still delivers every edge a consumer resolves.
    Building it builds every unit the named targets reach. Analysis downloads
    each named target's closure, so it names a few.
    """,
    attrs = {
        "packages": attr.label_list(mandatory = True, providers = [NpmPackagesInfo]),
        "exec_packages": attr.label_list(
            cfg = "exec",
            providers = [NpmPackagesInfo],
            doc = "Targets built as a tool is, whose units and checks the test builds in the exec configuration.",
        ),
        "checks": attr.label_list(doc = "Further targets whose outputs the test builds, such as the hub's `:install_exemptions`."),
    },
    test = True,
)
