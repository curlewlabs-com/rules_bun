"""Frozen Bun acquisition from explicitly supplied tools and workspace inputs."""

def _bun_install_impl(ctx):
    inputs = {"package.json": ctx.attr.package_json, "bun.lock": ctx.attr.lock}
    for label, destination in ctx.attr.inputs.items():
        if destination in inputs or "\\" in destination or any([part in ("", ".", "..") for part in destination.split("/")]):
            fail("duplicate or non-relative install input: " + destination)
        inputs[destination] = label
    for destination, label in inputs.items():
        ctx.file("workspace/" + destination, ctx.read(label), executable = False)

    # Passing paths to a subprocess does not watch the files it reads.
    for tool in [ctx.attr.bun, ctx.attr.node, ctx.attr._installer]:
        ctx.watch(tool)
    ctx.symlink(ctx.path(ctx.attr.bun), "tools/bun")
    ctx.symlink(ctx.path(ctx.attr.node), "tools/node")
    ctx.file("request.json", json.encode({
        "inputs": sorted(inputs.keys()),
        "workspaces": ctx.attr.workspaces,
    }), executable = False)
    result = ctx.execute(
        [ctx.path(ctx.attr.node), ctx.path(ctx.attr._installer), ctx.path(".")],
        environment = {"NODE_OPTIONS": "", "NODE_PATH": ""},
        timeout = ctx.attr.timeout,
        quiet = False,
    )
    if result.return_code:
        fail("Bun acquisition failed:\n" + result.stdout + "\n" + result.stderr)

    # Symlink topology is carried by the manifest, not traversed by Bazel globs.
    files = json.decode(ctx.read("files.json"))
    ctx.file("BUILD.bazel", """package(default_visibility = ["//visibility:public"])
exports_files(["closure.json"])
filegroup(name = "files", srcs = %s + ["closure.json"])
""" % repr(files), executable = False)

bun_install = repository_rule(
    implementation = _bun_install_impl,
    attrs = {
        "bun": attr.label(mandatory = True, allow_single_file = True, doc = "Bazel-acquired host Bun binary."),
        "node": attr.label(mandatory = True, allow_single_file = True, doc = "Bazel-acquired host Node binary; lifecycle scripts retain Node semantics."),
        "package_json": attr.label(mandatory = True, allow_single_file = True, doc = "Unmodified root package.json."),
        "lock": attr.label(mandatory = True, allow_single_file = True, doc = "Committed bun.lock, checked by Bun's frozen installer."),
        "inputs": attr.label_keyed_string_dict(allow_files = True, doc = "Workspace manifests and install configuration mapped to root-relative destinations."),
        "workspaces": attr.string_list(default = ["."], doc = "Exact workspace directories selected for this consumer; '.' selects root dependencies."),
        "timeout": attr.int(default = 600, doc = "Acquisition timeout in seconds, including lifecycle scripts."),
        "_installer": attr.label(default = Label("//bun:install.mjs")),
    },
    doc = "Acquire a consumer's frozen Bun workspace closure, with private installer state and no ambient Node executable.",
)
