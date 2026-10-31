"""A bun:test suite whose sources import nothing from npm, for this module's own translator tests."""

def _bun_unit_test_impl(ctx):
    tests = [src for src in ctx.files.srcs if src.basename.endswith(".test.ts")]
    if not tests:
        fail("bun_unit_test needs at least one .test.ts source")
    bun = ctx.attr._bun[DefaultInfo].files_to_run.executable
    executable = ctx.actions.declare_file(ctx.label.name)
    ctx.actions.write(
        output = executable,
        content = "\n".join([
            "#!/bin/sh",
            "set -eu",
            # A test that resolves a package from the network or a host tool
            # instead of its sources fails here rather than passing by luck.
            'PATH="${TEST_TMPDIR}/no-host-tools" BUN_RUNTIME_TRANSPILER_CACHE_PATH=0 HOME="${TEST_TMPDIR}" exec ./%s test --no-install %s' % (
                bun.short_path,
                " ".join(["./" + test.short_path for test in tests]),
            ),
            "",
        ]),
        is_executable = True,
    )
    return [DefaultInfo(
        executable = executable,
        runfiles = ctx.runfiles(files = ctx.files.srcs + [bun]).merge(ctx.attr._bun[DefaultInfo].default_runfiles),
    )]

bun_unit_test = rule(
    implementation = _bun_unit_test_impl,
    attrs = {
        "srcs": attr.label_list(allow_files = [".ts"], mandatory = True),
        "_bun": attr.label(
            default = Label("//npm:bun"),
            allow_single_file = True,
            cfg = "target",
            executable = True,
        ),
    },
    test = True,
)
