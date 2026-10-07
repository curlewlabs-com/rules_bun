"""Exercise the repository rule with real packages and relocated consumers."""

import argparse
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", default="bazel")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    command = [args.bazel]
    node, bun = runtimes(command, root)
    for repository_name, extra in [
        ("test_npm", []),
        ("test_workspace_only", ["--workspace-only"]),
    ]:
        subprocess.run(
            [*command, "build", f"@{repository_name}//:files"], cwd=root, check=True
        )
        execution_root = subprocess.check_output(
            [*command, "info", "execution_root"], cwd=root, text=True
        ).strip()
        manifest = subprocess.check_output(
            [
                *command,
                "cquery",
                f"@{repository_name}//:closure.json",
                "--output=files",
            ],
            cwd=root,
            text=True,
        ).strip()
        repository = (Path(execution_root) / manifest).resolve().parent
        subprocess.run(
            [
                str(node),
                str(root / "tests/verify.mjs"),
                str(repository),
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
        ("test_missing_workspace", "Missing declared workspace manifest"),
        ("test_tampered_integrity", "Checksum was"),
        ("test_unsupported_source", "Unsupported lock entry"),
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
