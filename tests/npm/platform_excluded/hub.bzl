"""A second npm hub, over the hand-written graph beside this file.

The npm extension takes one graph, the root module's, which is translated
from the fixture lock; the shapes this graph holds appear in no lock the
fixture can use, so this extension declares a hub over it as the npm
extension declares its own.
"""

load("//npm:hub.bzl", "npm_repositories")

def _hub_impl(module_ctx):
    npm_repositories(module_ctx, "npm_platform_excluded", Label(":npm_graph.json"))
    return module_ctx.extension_metadata(
        root_module_direct_deps = [],
        root_module_direct_dev_deps = ["npm_platform_excluded"],
        reproducible = True,
    )

hub = module_extension(implementation = _hub_impl)
