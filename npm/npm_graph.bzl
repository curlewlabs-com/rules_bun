"""The npm graph from bun.lock: its translation, its drift test, and its test against Bun's own install.

npm_graph translates a lock into the graph the npm extension reads
(docs/npm.md sec."Translation"), and checks that the checked-in graph is what
the translator writes from the current lock. npm_bun_install_test installs
the same lock with Bun and compares Bun's store with the graph.
"""

load(":npm_package.bzl", "NpmTarballsInfo")

_TRANSLATOR = [Label("//npm:npm_graph.ts"), Label("//npm:npm_lock.ts")]

def _shell_quote(value):
    return "'" + value.replace("'", "'\\''") + "'"

def _npm_graph_render_impl(ctx):
    out = ctx.actions.declare_file(ctx.label.name + ".json")
    translator = ctx.files._translator
    ctx.actions.run(
        executable = ctx.executable._bun,
        arguments = ["--no-install", translator[0].path, ctx.file.lock.path, ctx.file.bunfig.path, out.path],
        inputs = translator + [ctx.file.lock, ctx.file.bunfig],
        outputs = [out],
        # No PATH, so the translator reaches no host tool, and no transpiler
        # cache, which would write under HOME.
        env = {
            "BUN_RUNTIME_TRANSPILER_CACHE_PATH": "0",
            "HOME": out.dirname,
            "PATH": "",
        },
        mnemonic = "NpmGraph",
        progress_message = "Translating %{label}'s lock",
    )
    return [DefaultInfo(files = depset([out]))]

_npm_graph_render = rule(
    implementation = _npm_graph_render_impl,
    attrs = {
        "lock": attr.label(mandatory = True, allow_single_file = ["bun.lock"]),
        "bunfig": attr.label(mandatory = True, allow_single_file = ["bunfig.toml"]),
        "_translator": attr.label_list(default = _TRANSLATOR, allow_files = True),
        "_bun": attr.label(default = Label("//npm:bun"), executable = True, allow_single_file = True, cfg = "exec"),
    },
)

def _npm_graph_script(ctx, rendered, content):
    executable = ctx.actions.declare_file(ctx.label.name + ".sh")
    ctx.actions.write(executable, content, is_executable = True)
    return [DefaultInfo(
        executable = executable,
        runfiles = ctx.runfiles(files = [rendered, ctx.file.out]),
    )]

def _npm_graph_check_impl(ctx):
    rendered = ctx.file.rendered
    return _npm_graph_script(ctx, rendered, """#!/bin/sh
set -eu
if ! cmp -s {rendered} {out}; then
  diff -u {out} {rendered} || true
  echo {message} >&2
  exit 1
fi
""".format(
        rendered = _shell_quote(rendered.short_path),
        out = _shell_quote(ctx.file.out.short_path),
        message = _shell_quote("%s does not match %s. Run 'bazel run %s' to regenerate it." % (ctx.file.out.short_path, ctx.attr.lock_name, ctx.attr.update)),
    ))

_npm_graph_check_test = rule(
    implementation = _npm_graph_check_impl,
    attrs = {
        "rendered": attr.label(mandatory = True, allow_single_file = True),
        "out": attr.label(mandatory = True, allow_single_file = True),
        "lock_name": attr.string(mandatory = True),
        "update": attr.string(mandatory = True),
    },
    test = True,
)

def _npm_graph_update_impl(ctx):
    rendered = ctx.file.rendered
    if ctx.file.out.owner.workspace_name:
        fail("npm_graph writes a graph in the main repository; %s is not" % ctx.file.out.owner)
    return _npm_graph_script(ctx, rendered, """#!/bin/sh
set -eu
: "${{BUILD_WORKSPACE_DIRECTORY:?run this with bazel run}}"
cp {rendered} "$BUILD_WORKSPACE_DIRECTORY"/{out}
chmod 644 "$BUILD_WORKSPACE_DIRECTORY"/{out}
echo "Wrote $BUILD_WORKSPACE_DIRECTORY/"{out}
""".format(
        rendered = _shell_quote(rendered.short_path),
        out = _shell_quote(ctx.file.out.short_path),
    ))

_npm_graph_update = rule(
    implementation = _npm_graph_update_impl,
    attrs = {
        "rendered": attr.label(mandatory = True, allow_single_file = True),
        "out": attr.label(mandatory = True, allow_single_file = True),
    },
    executable = True,
)

def npm_graph(name, lock, bunfig, out, **kwargs):
    """The graph of `lock`, checked in at `out`.

    `bazel run :<name>` writes the translator's output to `out`, and
    `:<name>_test` fails while `out` differs from it. The npm extension's
    `graph` tag names `out`.

    Args:
      name: the writer's name; the test is `<name>_test`.
      lock: the `bun.lock` to translate.
      bunfig: the `bunfig.toml` beside it, whose `install.hoistPattern` names
        what Bun's isolated store hoists (docs/npm.md sec."Translation").
      out: the checked-in graph, a source file in this package.
      **kwargs: common attributes, such as `visibility` and `tags`, given to
        each target.
    """
    _npm_graph_render(name = name + "_rendered", lock = lock, bunfig = bunfig, **kwargs)
    _npm_graph_update(name = name, rendered = name + "_rendered", out = out, **kwargs)
    _npm_graph_check_test(
        name = name + "_test",
        rendered = name + "_rendered",
        out = out,
        lock_name = str(lock),
        update = "//%s:%s" % (native.package_name(), name),
        size = "small",
        **kwargs
    )

def _npm_bun_install_test_impl(ctx):
    root = ctx.file.lock.dirname
    files = {}
    for file in [ctx.file.lock, ctx.file.bunfig] + ctx.files.manifests:
        if file.dirname != root and not file.dirname.startswith(root + "/") and root:
            fail("%s is outside the lock's directory, %s" % (file.short_path, root))
        files[file.path.removeprefix(root).removeprefix("/")] = file.short_path
    tarballs = ctx.attr.tarballs[NpmTarballsInfo].tarballs
    inputs = ctx.actions.declare_file(ctx.label.name + ".inputs.json")
    ctx.actions.write(inputs, json.encode({
        "files": files,
        "tarballs": {name: file.short_path for name, file in tarballs.items()},
    }))
    runner = ctx.attr._runner[DefaultInfo].files_to_run.executable
    bun = ctx.attr._bun[DefaultInfo].files_to_run.executable
    executable = ctx.actions.declare_file(ctx.label.name + ".sh")
    ctx.actions.write(executable, """#!/bin/sh
set -eu
exec {runner} --graph {graph} --inputs {inputs} --bun {bun}
""".format(
        runner = _shell_quote(runner.short_path),
        graph = _shell_quote(ctx.file.graph.short_path),
        inputs = _shell_quote(inputs.short_path),
        bun = _shell_quote(bun.short_path),
    ), is_executable = True)
    runfiles = ctx.runfiles(
        files = [inputs, runner, bun, ctx.file.graph, ctx.file.lock, ctx.file.bunfig] + ctx.files.manifests + tarballs.values(),
    )
    for tool in [ctx.attr._runner, ctx.attr._bun]:
        runfiles = runfiles.merge(tool[DefaultInfo].default_runfiles)
    return [DefaultInfo(executable = executable, runfiles = runfiles)]

npm_bun_install_test = rule(
    implementation = _npm_bun_install_test_impl,
    doc = """Installs `lock` with Bun's isolated linker and compares Bun's store with `graph`.

    The translator models how Bun links packages rather than letting Bun do
    it, so this is what notices a Bun release that links peers differently, or
    a lock whose shape the model does not cover (docs/npm.md sec."Testing the
    translation"). Bun installs from a loopback registry serving only the
    hub's tarballs for the target platform, which Bazel downloaded against the
    lock's integrity, so the test reaches no network. Install scripts do not
    run, as they run in no unit.
    """,
    attrs = {
        "graph": attr.label(mandatory = True, allow_single_file = True, doc = "The checked-in graph of `lock`."),
        "lock": attr.label(mandatory = True, allow_single_file = ["bun.lock"]),
        "bunfig": attr.label(mandatory = True, allow_single_file = ["bunfig.toml"]),
        "manifests": attr.label_list(
            mandatory = True,
            allow_files = ["package.json"],
            doc = "The root package.json and every workspace's, each at its path from the lock's directory.",
        ),
        "tarballs": attr.label(mandatory = True, providers = [NpmTarballsInfo], doc = "The hub's `:tarballs`."),
        "_runner": attr.label(default = Label("//npm:npm_bun_install"), executable = True, cfg = "target"),
        "_bun": attr.label(default = Label("//npm:bun"), executable = True, allow_single_file = True, cfg = "target"),
    },
    test = True,
)
