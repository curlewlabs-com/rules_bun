"""A Bun launcher that finds its binary from the path it was invoked by.

docs/npm.md lets //npm:bun name any executable target, and a consumer's is
often a script like this one: a wrapper that adds a loader or runtime finds
its files relative to its runfiles tree, not its own resolved location. This
module's tests run Bun through it so that a rule which leaves the runfiles tree
before invoking Bun, as resolving the symlink Bazel stages there does, fails
here rather than in a consumer.
"""

def _bun_launcher_impl(ctx):
    launcher = ctx.actions.declare_file(ctx.label.name)
    short_path = launcher.short_path
    ctx.actions.write(launcher, """#!/bin/sh
set -eu
if [ -d "$0.runfiles/{workspace}" ]; then
    root="$0.runfiles/{workspace}"
else
    case "$0" in
        */{short_path}) root=${{0%/{short_path}}} ;;
        {short_path}) root=. ;;
        *)
            echo "bun launcher: no runfiles tree at $0" >&2
            exit 126
            ;;
    esac
fi
exec "$root/{bun}" "$@"
""".format(
        bun = ctx.file.bun.short_path,
        short_path = short_path,
        workspace = ctx.workspace_name,
    ), is_executable = True)
    return [DefaultInfo(
        executable = launcher,
        runfiles = ctx.runfiles(files = [ctx.file.bun]),
    )]

bun_launcher = rule(
    implementation = _bun_launcher_impl,
    attrs = {
        "bun": attr.label(mandatory = True, allow_single_file = True),
    },
    executable = True,
)
