"""The `npm` module extension: one repository per locked release, and a hub of graph targets.

The extension reads the dependency graph `npm_graph` writes from `bun.lock`
(docs/npm.md), never the lock itself, so every repository it declares is a
function of checked-in files alone.
"""

load(":hub.bzl", "npm_repositories")

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
    npm_repositories(ctx, tag.name, tag.graph, tag.exemptions)

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
