"""Exercise the repository rule with real packages and relocated consumers."""

import argparse
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", default="bazel")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    command = [args.bazel]
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
                str(repository / "tools/node"),
                str(root / "tests/verify.mjs"),
                str(repository),
                *extra,
            ],
            cwd=root,
            check=True,
        )
    # A successful fallback install would silently accept undeclared inputs.
    for repository_name, diagnostic in [
        ("test_lock_mismatch", "lockfile had changes, but lockfile is frozen"),
        ("test_missing_workspace", "Missing declared workspace manifest"),
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
