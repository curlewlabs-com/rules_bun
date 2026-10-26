"""Frozen Bun acquisition from explicitly supplied tools and workspace inputs."""

# Locked default-registry entries name their tarballs only relative to this.
_REGISTRY = "https://registry.npmjs.org/"

# npm's platform names; they reach the planner and Bun's command line.
_PLATFORM_CHARACTERS = "abcdefghijklmnopqrstuvwxyz0123456789"

def _bun_install_impl(ctx):
    for attribute, value in [("os", ctx.attr.os), ("cpu", ctx.attr.cpu)]:
        if value and not all([character in _PLATFORM_CHARACTERS for character in value.elems()]):
            fail("%s must be an npm platform name such as darwin, linux, arm64 or x64: %r" % (attribute, value))
    inputs = {"package.json": ctx.attr.package_json, "bun.lock": ctx.attr.lock}
    for label, destination in ctx.attr.inputs.items():
        if destination in inputs or "\\" in destination or any([part in ("", ".", "..") for part in destination.split("/")]):
            fail("duplicate or non-relative install input: " + destination)
        inputs[destination] = label
    for destination, label in inputs.items():
        ctx.file("workspace/" + destination, ctx.read(label), executable = False)

    # Passing paths to a subprocess does not watch the files it reads.
    for script in [ctx.attr._archive, ctx.attr._installer, ctx.attr._layout, ctx.attr._planner]:
        ctx.watch(script)

    # Validating a cached repository hashes every watched file, and a runtime
    # binary dwarfs the rest. Its identity file keys it instead; a read, unlike
    # a watch, fails on a missing file, which would leave the runtime unkeyed.
    for runtime, identity in [(ctx.attr.bun, ctx.attr.bun_identity), (ctx.attr.node, ctx.attr.node_identity)]:
        if identity:
            ctx.read(identity, watch = "yes")
        else:
            ctx.watch(runtime)
    ctx.symlink(ctx.path(ctx.attr.bun), "tools/bun")
    ctx.symlink(ctx.path(ctx.attr.node), "tools/node")
    ctx.file("request.json", json.encode({
        "inputs": sorted(inputs.keys()),
        "workspaces": ctx.attr.workspaces,
        "os": ctx.attr.os,
        "cpu": ctx.attr.cpu,
    }), executable = False)

    # A private HOME keeps a user's global Bun configuration out of planning.
    private = str(ctx.path("scratch/planner"))
    planned = ctx.execute(
        [ctx.path(ctx.attr.bun), "--no-install", ctx.path(ctx.attr._planner), ctx.path(".")],
        environment = {"HOME": private, "XDG_CONFIG_HOME": private, "NODE_OPTIONS": "", "NODE_PATH": ""},
        quiet = False,
    )
    if planned.return_code:
        fail("Bun lock planning failed:\n" + planned.stdout + "\n" + planned.stderr)

    # Bazel's downloader verifies each tarball against its locked integrity and
    # keeps it in the repository cache, so a host fetches it once across closures.
    pending = [
        ctx.download(
            url = _REGISTRY + tarball["path"],
            output = "registry/" + tarball["path"],
            integrity = tarball["integrity"],
            block = False,
        )
        for tarball in json.decode(ctx.read("tarballs.json"))
    ]
    for download in pending:
        download.wait()

    result = ctx.execute(
        [ctx.path(ctx.attr.node), ctx.path(ctx.attr._installer), ctx.path(".")],
        environment = {"NODE_OPTIONS": "", "NODE_PATH": ""},
        timeout = ctx.attr.timeout,
        quiet = False,
    )
    if result.return_code:
        fail("Bun acquisition failed:\n" + result.stdout + "\n" + result.stderr)

    # Symlink topology is carried by the manifest, not traversed by Bazel globs.
    # tools.json stays out of :files: hosts on different platforms acquire the
    # same closure with different runtime binaries, and an action reading their
    # digests would be keyed by the host that acquired its inputs.
    # :archive is the closure as one input; :files, one input per installed
    # file, stays for consumers still restoring from it and is deprecated.
    files = json.decode(ctx.read("files.json"))
    ctx.file("BUILD.bazel", """package(default_visibility = ["//visibility:public"])
exports_files(["closure.json", "closure.tar", "tools.json"])
filegroup(name = "archive", srcs = ["closure.tar"])
filegroup(name = "files", srcs = %s + ["closure.json"])
""" % repr(files), executable = False)

    # The runtime links point into this output base's other repositories, and the
    # private package cache duplicates the installed tree. Neither is an output,
    # and a repository Bazel keeps for other workspaces must not carry either.
    ctx.delete("tools")
    ctx.delete("scratch")
    return ctx.repo_metadata(reproducible = ctx.attr.reproducible)

bun_install = repository_rule(
    implementation = _bun_install_impl,
    attrs = {
        "bun": attr.label(mandatory = True, allow_single_file = True, doc = "Bazel-acquired host Bun binary."),
        "node": attr.label(mandatory = True, allow_single_file = True, doc = "Bazel-acquired host Node binary; lifecycle scripts retain Node semantics."),
        "bun_identity": attr.label(allow_single_file = True, doc = "Optional small file whose content changes whenever the `bun` binary's bytes do, such as the pin it was downloaded from. When set, the rule watches this file instead of the binary, so checking a cached acquisition need not hash the binary; a binary replaced under an unchanged identity keeps the acquisition made with the old one. The file must exist."),
        "node_identity": attr.label(allow_single_file = True, doc = "Optional small file identifying the `node` binary, with the same contract as `bun_identity`."),
        "package_json": attr.label(mandatory = True, allow_single_file = True, doc = "Unmodified root package.json."),
        "lock": attr.label(mandatory = True, allow_single_file = True, doc = "Committed bun.lock, checked by Bun's frozen installer. Every package must come from the default npm registry with an integrity value; Bazel downloads those tarballs."),
        "inputs": attr.label_keyed_string_dict(allow_files = True, doc = "Workspace manifests and install configuration mapped to root-relative destinations."),
        "workspaces": attr.string_list(default = ["."], doc = "Exact workspace directories selected for this consumer; '.' selects root dependencies."),
        "timeout": attr.int(default = 600, doc = "Acquisition timeout in seconds, including lifecycle scripts."),
        "os": attr.string(doc = "npm operating system whose native optional packages the closure carries, such as `darwin` or `linux`, for a closure an action executes on another platform. Empty selects the acquisition host's. Lifecycle scripts still run on the acquisition host."),
        "cpu": attr.string(doc = "npm CPU architecture whose native optional packages the closure carries, such as `arm64` or `x64`, with the same contract as `os`."),
        "reproducible": attr.bool(default = False, doc = "Declare to Bazel that acquiring these inputs again would produce the same repository, so it may reuse the result from its repo contents cache in other workspaces and output bases instead of installing again. Set it only when every lifecycle output depends on the declared inputs alone, not on the repository's absolute path, the time, or other host state."),
        "_archive": attr.label(default = Label("//bun:archive.mjs")),
        "_installer": attr.label(default = Label("//bun:install.mjs")),
        "_layout": attr.label(default = Label("//bun:layout.mjs")),
        "_planner": attr.label(default = Label("//bun:plan.mjs")),
    },
    doc = "Acquire a consumer's frozen Bun workspace closure, with private installer state and no ambient Node executable. Bazel downloads the locked registry tarballs; Bun installs them from a loopback registry.",
)
