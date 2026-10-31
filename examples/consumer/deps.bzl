"""The consumer owns runtime selection and installation inputs."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")
load("@curlewlabs_rules_bun//bun:repositories.bzl", "bun_install")

_BUN = {
    "mac os x/aarch64": ("darwin-aarch64", "90987a3a16d7db556d886ac3d551e7b6d3edf0a1cf43acaed622e8676be1d12f", "@nodejs_darwin_arm64//:bin/nodejs/bin/node"),
    "mac os x/x86_64": ("darwin-x64", "80520d7e17526308c9185d261679ac6d27798d3803a0e9f7ff9121ab8affb012", "@nodejs_darwin_amd64//:bin/nodejs/bin/node"),
    "linux/aarch64": ("linux-aarch64", "54328bbc2d9c8e0c9f892c544d66c57a83b84139e34909e5ee81758f1ac8fda7", "@nodejs_linux_arm64//:bin/nodejs/bin/node"),
    # Baseline avoids requiring AVX2 merely to acquire packages on x86 hosts.
    "linux/amd64": ("linux-x64-baseline", "c678040f14fe0440eb839d37cbd0ce4c051a32da72806ac97de6a6aab6bf728f", "@nodejs_linux_amd64//:bin/nodejs/bin/node"),
}

def _deps(ctx):
    platform = ctx.os.name + "/" + ctx.os.arch
    if platform not in _BUN:
        fail("Unsupported consumer host: " + platform)
    archive, sha256, node = _BUN[platform]
    http_archive(
        name = "consumer_bun",
        urls = ["https://github.com/oven-sh/bun/releases/download/bun-v1.4.2/bun-" + archive + ".zip"],
        sha256 = sha256,
        strip_prefix = "bun-" + archive,
        build_file_content = 'exports_files(["bun"])',
    )
    bun_install(
        name = "npm",
        bun = "@consumer_bun//:bun",
        node = node,
        package_json = "//:package.json",
        lock = "//:bun.lock",
        inputs = {"//:lifecycle.cjs": "lifecycle.cjs"},
    )

deps = module_extension(implementation = _deps, os_dependent = True, arch_dependent = True)
