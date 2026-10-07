"""Real package acquisition for this module's integration example."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")
load("//bun:repositories.bzl", "bun_install")

_BUN = {
    "mac os x/aarch64": ("darwin-aarch64", "82034e87c9d9b4398ea619aee2eed5d2a68c8157e9a6ae2d1052d84d533ccd8d", "@nodejs_darwin_arm64//:bin/nodejs/bin/node"),
    "mac os x/x86_64": ("darwin-x64", "c1d90bf6140f20e572c473065dc6b37a4b036349b5e9e4133779cc642ad94323", "@nodejs_darwin_amd64//:bin/nodejs/bin/node"),
    "linux/aarch64": ("linux-aarch64", "fa5ecb25cafa8e8f5c87a0f833719d46dd0af0a86c7837d806531212d55636d3", "@nodejs_linux_arm64//:bin/nodejs/bin/node"),
    # Baseline avoids requiring AVX2 merely to acquire packages on x86 hosts.
    "linux/amd64": ("linux-x64-baseline", "41201a8c5ee74a9dcbb1ce25a1104f1f929838b57a845aa78d98379b0ce7cde2", "@nodejs_linux_amd64//:bin/nodejs/bin/node"),
}

def _deps(ctx):
    platform = ctx.os.name + "/" + ctx.os.arch
    if platform not in _BUN:
        fail("Unsupported acquisition test host: " + platform)
    archive, sha256, node = _BUN[platform]
    http_archive(
        name = "test_bun",
        urls = ["https://github.com/oven-sh/bun/releases/download/bun-v1.3.10/bun-" + archive + ".zip"],
        sha256 = sha256,
        strip_prefix = "bun-" + archive,
        build_file_content = 'exports_files(["bun"])',
    )
    bun_install(
        name = "test_npm",
        bun = "@test_bun//:bun",
        node = node,
        package_json = "//:package.json",
        lock = "//:bun.lock",
        inputs = {"//:examples/workspace/package.json": "examples/workspace/package.json"},
        workspaces = [".", "examples/workspace"],
    )

    bun_install(
        name = "test_workspace_only",
        bun = "@test_bun//:bun",
        node = node,
        package_json = "//:package.json",
        lock = "//:bun.lock",
        inputs = {"//:examples/workspace/package.json": "examples/workspace/package.json"},
        workspaces = ["examples/workspace"],
    )
    bun_install(
        name = "test_lock_mismatch",
        bun = "@test_bun//:bun",
        node = node,
        package_json = "//tests:mismatched.package.json",
        lock = "//:bun.lock",
        inputs = {"//:examples/workspace/package.json": "examples/workspace/package.json"},
    )

    # An alias beside the package it renames, and a bin inside a dependency
    # cycle: the two parts of Bun's isolated layout its install threads decide.
    bun_install(
        name = "test_layout",
        bun = "@test_bun//:bun",
        node = node,
        package_json = "//tests:layout/package.json",
        lock = "//tests:layout/bun.lock",
        # Bun installs a root without workspaces into a hoisted tree otherwise.
        inputs = {"//tests:layout/bunfig.toml": "bunfig.toml"},
    )

    # Bazel must refuse tarball bytes that disagree with the locked integrity.
    bun_install(
        name = "test_tampered_integrity",
        bun = "@test_bun//:bun",
        node = node,
        package_json = "//tests:rejections/tampered/package.json",
        lock = "//tests:rejections/tampered/bun.lock",
    )

    # A source outside the default registry would be fetched outside Bazel.
    bun_install(
        name = "test_unsupported_source",
        bun = "@test_bun//:bun",
        node = node,
        package_json = "//tests:rejections/unsupported/package.json",
        lock = "//tests:rejections/unsupported/bun.lock",
    )

    # Bun lets one hoist pattern replace another, so a workspace's would
    # silently undo the acquisition's or be undone by it.
    bun_install(
        name = "test_hoist_pattern",
        bun = "@test_bun//:bun",
        node = node,
        package_json = "//:package.json",
        lock = "//:bun.lock",
        inputs = {
            "//:examples/workspace/package.json": "examples/workspace/package.json",
            "//tests:rejections/hoisted.npmrc": ".npmrc",
        },
    )
    bun_install(
        name = "test_missing_workspace",
        bun = "@test_bun//:bun",
        node = node,
        package_json = "//:package.json",
        lock = "//:bun.lock",
    )

deps = module_extension(implementation = _deps, os_dependent = True, arch_dependent = True)
