"""Real alternate runtimes for repository invalidation checks."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

# Pin older releases so replacing runtime bytes has an observable version change.
# Bun digests: https://github.com/oven-sh/bun/releases/tag/bun-v1.3.9
# Node digests: https://nodejs.org/dist/v24.13.0/SHASUMS256.txt
_HOSTS = {
    "mac os x/aarch64": ("darwin-aarch64", "cde6a4edf19cf64909158fa5a464a12026fd7f0d79a4a950c10cf0af04266d85", "darwin-arm64", "d595961e563fcae057d4a0fb992f175a54d97fcc4a14dc2d474d92ddeea3b9f8"),
    "mac os x/x86_64": ("darwin-x64", "588f4a48740b9a0c366a00f878810ab3ab5e6734d29b7c3cbdd9484b74a007de", "darwin-x64", "6f03c1b48ddbe1b129a6f8038be08e0899f05f17185b4d3e4350180ab669a7f3"),
    "linux/aarch64": ("linux-aarch64", "a2c2862bcc1fd1c0b3a8dcdc8c7efb5e2acd871eb20ed2f17617884ede81c844", "linux-arm64", "0f6d40b94c6a2eb6b4c240ffc8b9fd3ada7ab044c177dd413c06e1ef9a63f081"),
    "linux/amd64": ("linux-x64-baseline", "104d4d037f4b35e10215c0507e1779691f39c57bd91ddeefe11cad781e3fc4b9", "linux-x64", "6223aad1a81f9d1e7b682c59d12e2de233f7b4c37475cd40d1c89c42b737ffa8"),
}

def _runtimes(ctx):
    bun_host, bun_sha, node_host, node_sha = _HOSTS[ctx.os.name + "/" + ctx.os.arch]
    http_archive(
        name = "previous_bun",
        urls = ["https://github.com/oven-sh/bun/releases/download/bun-v1.3.9/bun-" + bun_host + ".zip"],
        sha256 = bun_sha,
        strip_prefix = "bun-" + bun_host,
        build_file_content = 'exports_files(["bun"])',
    )
    http_archive(
        name = "previous_node",
        urls = ["https://nodejs.org/dist/v24.13.0/node-v24.13.0-" + node_host + ".tar.gz"],
        sha256 = node_sha,
        strip_prefix = "node-v24.13.0-" + node_host,
        build_file_content = 'exports_files(["bin/node"])',
    )

runtimes = module_extension(implementation = _runtimes, os_dependent = True, arch_dependent = True)
