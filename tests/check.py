"""Exercise the repository rule with real packages and relocated consumers."""

import argparse
import hashlib
import json
import platform
import subprocess
from pathlib import Path


def runtimes(command: list[str], root: Path) -> list[Path]:
    """The Node and Bun the test repositories declare, as a consumer selects them."""
    os_name = {"Darwin": "darwin", "Linux": "linux"}[platform.system()]
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}[platform.machine()]
    labels = [f"@nodejs_{os_name}_{arch}//:bin/nodejs/bin/node", "@test_bun//:bun"]
    subprocess.run([*command, "build", *labels], cwd=root, check=True)
    execution_root = subprocess.check_output(
        [*command, "info", "execution_root"], cwd=root, text=True
    ).strip()
    # Resolved now: a later build replaces the execution root's links with its own.
    return [
        (
            Path(execution_root)
            / subprocess.check_output(
                [*command, "cquery", label, "--output=files"], cwd=root, text=True
            ).strip()
        ).resolve()
        for label in labels
    ]


def repository_of(command: list[str], root: Path, repository_name: str) -> Path:
    subprocess.run(
        [*command, "build", f"@{repository_name}//:files"], cwd=root, check=True
    )
    execution_root = subprocess.check_output(
        [*command, "info", "execution_root"], cwd=root, text=True
    ).strip()
    manifest = subprocess.check_output(
        [*command, "cquery", f"@{repository_name}//:closure.json", "--output=files"],
        cwd=root,
        text=True,
    ).strip()
    return (Path(execution_root) / manifest).resolve().parent


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def exported(command: list[str], root: Path, label: str) -> dict[str, Path]:
    """Each file of a public target, keyed by its path in its repository."""
    subprocess.run([*command, "build", label], cwd=root, check=True)
    execution_root = Path(
        subprocess.check_output(
            [*command, "info", "execution_root"], cwd=root, text=True
        ).strip()
    )
    paths = subprocess.check_output(
        [*command, "cquery", label, "--output=files"], cwd=root, text=True
    ).splitlines()
    # cquery prints each as external/<repository>/<path>.
    return {
        "/".join(Path(path).parts[2:]): (execution_root / path).resolve()
        for path in paths
    }


def check_runtime_independence(
    command: list[str], root: Path, node: Path, bun: Path
) -> None:
    """Hosts on different platforms acquire the same closure with their own
    runtime binaries. Anything in :files that records which would key every
    action reading the closure by the host that acquired it."""
    files = []
    recorded = []
    for name in ["test_npm", "test_other_runtimes"]:
        exports = exported(command, root, f"@{name}//:files")
        files.append({path: digest(file) for path, file in exports.items()})
        (tools,) = exported(command, root, f"@{name}//:tools.json").values()
        recorded.append(json.loads(tools.read_text()))
    current, other = files
    assert current == other, sorted(
        path
        for path in current.keys() | other.keys()
        if current.get(path) != other.get(path)
    )
    assert recorded[0] == {"bun": digest(bun), "node": digest(node)}, recorded
    # Equal runtimes would let the comparison above pass without testing anything.
    assert all(
        recorded[0][runtime] != recorded[1][runtime] for runtime in recorded[0]
    ), recorded
    print("Verified closure files independent of the acquiring runtimes.", flush=True)


def check_layout(command: list[str], root: Path, node: Path) -> None:
    """The fixture's install leaves both timing-decided parts of Bun's layout
    to install threads; the closure must hold the one acquisition fixes."""
    subprocess.run(
        [str(node), "--test", str(root / "tests/layout_test.mjs")], cwd=root, check=True
    )
    closure = json.loads(
        (repository_of(command, root, "test_layout") / "closure.json").read_text()
    )
    links = {
        entry["path"]: entry["symlink"]
        for entry in closure["entries"]
        if "symlink" in entry
    }
    fallback = "node_modules/.bun/node_modules/"
    # The root depends on string-width 6.1.0 itself; @isaacs/cliui's
    # string-width-cjs alias names 4.2.3 and must not take the link.
    for name, version in [
        ("string-width", "6.1.0"),
        ("strip-ansi", "7.2.0"),
        ("wrap-ansi", "8.1.0"),
    ]:
        assert links[fallback + name] == f"../{name}@{version}/node_modules/{name}", (
            name,
            links[fallback + name],
        )
    assert not [
        path for path in links if path.startswith(fallback) and path.endswith("-cjs")
    ]
    # eslint depends on @eslint-community/eslint-utils, which peers on eslint.
    utils = sorted(
        path.removesuffix("/eslint")
        for path in links
        if path.startswith("node_modules/.bun/@eslint-community+eslint-utils@")
        and path.endswith("/node_modules/eslint")
    )
    assert utils, "fixture lost its eslint cycle"
    for modules in utils:
        assert links[f"{modules}/.bin/eslint"] == "../eslint/bin/eslint.js", modules
    print("Verified deterministic layout: test_layout", flush=True)


def check_cross_platform(command: list[str], root: Path) -> None:
    """A closure installed for the other supported OS carries that OS's native
    esbuild package and not the acquisition host's."""
    host_os = {"Darwin": "darwin", "Linux": "linux"}[platform.system()]
    host_cpu = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64"}[
        platform.machine()
    ]
    target = "linux-x64" if host_os == "darwin" else "darwin-arm64"
    closure = json.loads(
        (
            repository_of(command, root, "test_cross_platform") / "closure.json"
        ).read_text()
    )
    packages = {
        entry["path"].split("/")[2]
        for entry in closure["entries"]
        if entry["path"].startswith("node_modules/.bun/@esbuild+")
    }
    assert f"@esbuild+{target}@0.28.2" in packages, packages
    assert f"@esbuild+{host_os}-{host_cpu}@0.28.2" not in packages, packages
    print(f"Verified cross-platform closure: {target}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", default="bazel")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    command = [args.bazel]
    node, bun = runtimes(command, root)
    check_layout(command, root, node)
    check_cross_platform(command, root)
    check_runtime_independence(command, root, node, bun)
    for repository_name, extra in [
        ("test_npm", []),
        ("test_workspace_only", ["--workspace-only"]),
    ]:
        subprocess.run(
            [
                str(node),
                str(root / "tests/verify.mjs"),
                str(repository_of(command, root, repository_name)),
                str(bun),
                *extra,
            ],
            cwd=root,
            check=True,
        )
    # A successful fallback install would silently accept undeclared inputs.
    for repository_name, diagnostic in [
        # Offline, a manifest the lock does not satisfy asks for unlocked metadata.
        ("test_lock_mismatch", "Installation reached outside the locked tarballs"),
        ("test_hoist_pattern", "Unsupported hoist pattern in .npmrc"),
        ("test_missing_workspace", "Missing declared workspace manifest"),
        ("test_missing_identity", "missing.identity"),
        ("test_tampered_integrity", "Checksum was"),
        ("test_unsupported_source", "Unsupported lock entry"),
        ("test_invalid_platform", "os must be an npm platform name"),
    ]:
        result = subprocess.run(
            [*command, "build", f"@{repository_name}//:files"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if result.returncode == 0 or diagnostic not in result.stdout:
            raise RuntimeError(f"Expected acquisition rejection: {result.stdout}")
        print(f"Verified rejection: {repository_name}", flush=True)


if __name__ == "__main__":
    main()
