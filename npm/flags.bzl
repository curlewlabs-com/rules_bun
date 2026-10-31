"""The build settings the npm rules read."""

NpmFlagInfo = provider(
    doc = "A bool build setting's value.",
    fields = {"value": "The setting's value."},
)

def _bool_flag_impl(ctx):
    return [NpmFlagInfo(value = ctx.build_setting_value)]

# Bazel's own bool setting, without a dependency on bazel_skylib for its rule.
bool_flag = rule(
    implementation = _bool_flag_impl,
    build_setting = config.bool(flag = True),
)

def _unset_bun_impl(ctx):
    executable = ctx.actions.declare_file(ctx.label.name + ".sh")
    ctx.actions.write(executable, """#!/bin/sh
echo 'rules_bun runs the Bun --@curlewlabs_rules_bun//npm:bun names, and none is set (docs/npm.md sec."Setup")' >&2
exit 1
""", is_executable = True)
    return [DefaultInfo(executable = executable)]

# //npm:bun's default: analysis succeeds without a Bun, so a build that runs
# none needs no setting, and the first action that does run one fails naming
# the flag.
unset_bun = rule(
    implementation = _unset_bun_impl,
    executable = True,
)
