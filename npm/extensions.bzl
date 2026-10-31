"""The `npm` module extension: one repository per locked release, and a hub of graph targets.

The extension reads the dependency graph `npm_graph` writes from `bun.lock`
(docs/npm.md), never the lock itself, so every repository it declares is a
function of checked-in files alone.
"""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_file")
load(":platforms.bzl", "NPM_PLATFORMS", "npm_allows")

def _npm_hub_impl(rctx):
    graph = json.decode(rctx.read(rctx.attr.graph))
    packages = graph["packages"]
    repositories = graph["repositories"]
    exemptions = json.decode(rctx.read(rctx.attr.exemptions)) if rctx.attr.exemptions else {}
    releases = {}
    for name, repository in repositories.items():
        releases.setdefault(repository["name"], []).append(name)

    # An entry for a package the lock no longer holds would excuse nothing
    # anyone re-reviewed, so it fails every consumer until it is removed.
    stale = sorted([package for package in exemptions if package not in releases])
    if stale:
        fail("%s names packages no locked release has; remove them: %s" % (rctx.attr.exemptions, ", ".join(stale)))
    rule = str(Label("//npm:npm_package.bzl"))
    lines = [
        'load("%s", "declared_archive")' % str(Label("@curlewlabs_rules_archive_store//archive_store:archives.bzl")),
        'load("%s", "npm_install_exemption", "npm_link", "npm_package", "npm_package_cycle", "npm_release", "npm_tarballs")' % rule,
        "",
        'package(default_visibility = ["//visibility:public"])',
    ]

    # Per release, since the check and the archive unit read only the
    # tarball, so one of each serves all of the release's nodes. The archive
    # unit converts the tarball the check passed, which is the release's
    # default output.
    for name, repository in sorted(repositories.items()):
        lines.append("")
        lines.append("declared_archive(")
        lines.append("    name = %r," % ("_unit/" + name))
        lines.append("    archive = %r," % (":_release/" + name))
        lines.append("    integrity = %r," % repository["integrity"])
        lines.append("    strip_components = 1,")
        lines.append('    visibility = ["//visibility:private"],')
        lines.append(")")
        lines.append("")
        lines.append("npm_release(")
        lines.append("    name = %r," % ("_release/" + name))
        lines.append("    package = %r," % repository["name"])
        lines.append("    version = %r," % repository["version"])
        lines.append('    archive = "@%s//file",' % name)
        exempt = {step: entry["sha256"] for step, entry in exemptions.get(repository["name"], {}).items()}
        if exempt:
            lines.append("    exempt = %s," % json.encode(exempt))
        lines.append('    visibility = ["//visibility:private"],')
        lines.append(")")

    # The tarballs Bun downloads on each platform, which npm_bun_install_test
    # serves it: every locked release the platform admits, reached or not, so
    # a platform fetches no other platform's binaries. A release's os and cpu
    # are its own, so any of its nodes answers.
    constraints = {}
    for package in packages.values():
        constraints[package["repository"]] = (package.get("os", []), package.get("cpu", []))
    for platform in NPM_PLATFORMS:
        admitted = [
            name
            for name in sorted(repositories)
            if npm_allows(constraints.get(name, ([], []))[0], platform.os) and npm_allows(constraints.get(name, ([], []))[1], platform.cpu)
        ]
        lines.append("")
        lines.append("npm_tarballs(")
        lines.append("    name = %r," % ("_tarballs/" + platform.name))
        lines.append("    tarballs = %s," % json.encode({"@%s//file" % name: name for name in admitted}))
        lines.append('    visibility = ["//visibility:private"],')
        lines.append(")")
    lines.append("")
    lines.append("alias(")
    lines.append('    name = "tarballs",')
    lines.append("    actual = select(%s)," % json.encode({
        str(Label("//npm:" + platform.name)): ":_tarballs/" + platform.name
        for platform in NPM_PLATFORMS
    }))
    lines.append(")")
    for package, entries in sorted(exemptions.items()):
        lines.append("")
        lines.append("npm_install_exemption(")
        lines.append("    name = %r," % ("_exemption/" + package))
        lines.append("    package = %r," % package)
        lines.append("    exempt = %s," % json.encode({step: entry["sha256"] for step, entry in entries.items()}))
        lines.append("    releases = %s," % json.encode([":_release/" + name for name in sorted(releases[package])]))
        lines.append('    visibility = ["//visibility:private"],')
        lines.append(")")
    lines.append("")
    lines.append("filegroup(")
    lines.append('    name = "install_exemptions",')
    lines.append("    srcs = %s," % json.encode([":_exemption/" + package for package in sorted(exemptions)]))
    lines.append(")")
    hoisted = graph.get("hoisted", {})
    cycles = {}
    for node, package in packages.items():
        if "cycle" in package:
            cycles.setdefault(package["cycle"], []).append(node)
    for node, package in packages.items():
        cycle = package.get("cycle")
        name = "_cycle_member/" + node if cycle else node
        deps = {}
        for target in package["edges"].values():
            if cycle and packages[target].get("cycle") == cycle:
                continue
            deps[":" + target] = True
        lines.append("")
        lines.append("npm_package(")
        lines.append("    name = %r," % name)
        lines.append("    node = %r," % node)
        lines.append("    release = %r," % (":_release/" + package["repository"]))
        if hoisted.get(repositories[package["repository"]]["name"]) == node:
            lines.append("    hoisted = True,")
        lines.append("    declared = %r," % (":_unit/" + package["repository"]))
        for attribute in ("os", "cpu", "bin", "edges"):
            if package.get(attribute):
                lines.append("    %s = %s," % (attribute, json.encode(package[attribute])))

        # What an edge's link needs of the node it names, since a cycle's
        # members are not one another's deps.
        aliases = {}
        edge_constraints = {"os": {}, "cpu": {}}
        for dependency, target in package["edges"].items():
            name = repositories[packages[target]["repository"]]["name"]
            if name != dependency:
                aliases[dependency] = name
            for attribute, values in edge_constraints.items():
                if packages[target].get(attribute):
                    values[dependency] = packages[target][attribute]
        if aliases:
            lines.append("    aliases = %s," % json.encode(aliases))
        for attribute, values in edge_constraints.items():
            if values:
                lines.append("    edge_%s = %s," % (attribute, json.encode(values)))
        if deps:
            lines.append("    deps = %s," % json.encode(sorted(deps)))
        if cycle:
            lines.append('    visibility = ["//visibility:private"],')
        lines.append(")")
    for head, members in sorted(cycles.items()):
        lines.append("")
        lines.append("npm_package_cycle(")
        lines.append("    name = %r," % ("_cycle/" + head))
        lines.append("    members = %s," % json.encode([":_cycle_member/" + member for member in sorted(members)]))
        lines.append('    visibility = ["//visibility:private"],')
        lines.append(")")
        for member in sorted(members):
            lines.append("")
            lines.append('alias(name = %r, actual = ":_cycle/%s")' % (member, head))

    # What each workspace links under each dependency name, by the path Node
    # would find it at, so a consumer names a tool without naming its version.
    for directory, edges in graph["workspaces"].items():
        prefix = "" if directory == "." else directory + "/"

        # Two dependencies can name one bin, as an `npm:` alias of a package
        # the workspace also depends on does. Bun links the name to the
        # dependency whose name sorts first, so each bin is that one's alone,
        # and npm_bun_install_test compares the choice with Bun's.
        owners = {}
        for dependency, target in sorted(edges.items()):
            if not target.startswith("workspace:"):
                for name in sorted(packages[target].get("bin", {})):
                    owners.setdefault(name, dependency)
        for dependency, target in edges.items():
            if target.startswith("workspace:"):
                continue
            package = packages[target]
            for name, path in package.get("bin", {}).items():
                # A bin's link must land on the package's own file.
                if "/" in name or path.startswith("/") or ".." in path.split("/"):
                    fail("%s: %s's bin %r -> %r leaves its package" % (rctx.attr.graph, target, name, path))
            lines.append("")
            lines.append("npm_link(")
            lines.append("    name = %r," % (prefix + "node_modules/" + dependency))
            lines.append("    workspace = %r," % directory)
            lines.append("    dependency = %r," % dependency)
            lines.append("    package = %r," % (":" + target))
            lines.append("    node = %r," % target)
            lines.append("    package_name = %r," % repositories[package["repository"]]["name"])
            for attribute in ("os", "cpu"):
                if package.get(attribute):
                    lines.append("    %s = %s," % (attribute, json.encode(package[attribute])))
            bins = {name: path for name, path in package.get("bin", {}).items() if owners[name] == dependency}
            if bins:
                lines.append("    bin = %s," % json.encode(bins))
            lines.append(")")
    rctx.file("BUILD.bazel", "\n".join(lines) + "\n")

    # Each workspace's registry links, and its dependencies on other
    # workspaces by name and directory, for a consumer that runs a workspace's
    # own scripts or tests: it takes that workspace's links and lays out its
    # workspace dependencies from its own sources, since no link in the hub
    # can reach them (docs/npm.md sec."Consumers").
    links = {}
    workspace_deps = {}
    for directory, edges in sorted(graph["workspaces"].items()):
        prefix = "" if directory == "." else directory + "/"
        links[directory] = sorted(["@%s//:%snode_modules/%s" % (rctx.attr.hub, prefix, dependency) for dependency, target in edges.items() if not target.startswith("workspace:")])
        workspace_deps[directory] = {dependency: target.removeprefix("workspace:") for dependency, target in sorted(edges.items()) if target.startswith("workspace:")}
    rctx.file("workspaces.bzl", "\n".join([
        '"""Generated from %s by the npm extension."""' % rctx.attr.graph.name,
        "",
        "# A workspace's directory to the hub links of its registry dependencies.",
        "NPM_WORKSPACE_LINKS = %s" % json.encode(links),
        "",
        "# A workspace's directory to its workspace dependencies: name to directory.",
        "NPM_WORKSPACE_DEPS = %s" % json.encode(workspace_deps),
        "",
    ]))
    return rctx.repo_metadata(reproducible = True)

# The graph's targets, generated from the checked-in graph and install
# exemptions rather than the lock, so this repository is defined by those files
# and not by every package. A node's target is named by its graph id.
_npm_hub = repository_rule(
    implementation = _npm_hub_impl,
    attrs = {
        "graph": attr.label(mandatory = True, allow_single_file = True),
        "exemptions": attr.label(allow_single_file = True),
        "hub": attr.string(mandatory = True, doc = "The hub's apparent name, which workspaces.bzl's labels use."),
    },
)

def _npm_impl(ctx):
    tags = []
    for mod in ctx.modules:
        if mod.tags.graph and not mod.is_root:
            fail("npm.graph belongs to the root module; %s declares one" % mod.name)
        tags += mod.tags.graph
    if len(tags) > 1:
        fail("npm.graph is declared %d times; one graph serves a module" % len(tags))
    if not tags:
        return ctx.extension_metadata(root_module_direct_deps = [], root_module_direct_dev_deps = [], reproducible = True)
    tag = tags[0]

    # One download per locked release, verified by Bazel's downloader against
    # the lock's integrity. Each definition names only its own release, so a
    # bump redefines only the repositories of the releases it changed.
    for name, repository in json.decode(ctx.read(tag.graph))["repositories"].items():
        http_file(
            name = name,
            urls = [repository["url"]],
            integrity = repository["integrity"],
            downloaded_file_path = "package.tgz",
        )
    _npm_hub(name = tag.name, graph = tag.graph, exemptions = tag.exemptions, hub = tag.name)

    # Every definition is a function of checked-in files alone, so the
    # module lock need not record them, and it would otherwise hold one entry
    # per locked release.
    dev = ctx.is_dev_dependency(tag)
    return ctx.extension_metadata(
        root_module_direct_deps = [] if dev else [tag.name],
        root_module_direct_dev_deps = [tag.name] if dev else [],
        reproducible = True,
    )

_graph = tag_class(
    doc = "The root module's npm dependency graph.",
    attrs = {
        "name": attr.string(default = "npm", doc = "The hub repository's name, which `use_repo` imports."),
        "graph": attr.label(mandatory = True, doc = "The graph `npm_graph` writes from `bun.lock`."),
        "exemptions": attr.label(doc = "A JSON file exempting install steps by package (docs/npm.md sec.\"Install steps\"); none is exempt without it."),
    },
)

npm = module_extension(
    implementation = _npm_impl,
    doc = "Declares a repository per locked npm release, and a hub of graph targets over them.",
    tag_classes = {"graph": _graph},
)
