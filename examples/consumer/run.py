"""Acquire through public labels and export a standalone runnable consumer."""

import argparse
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path


def acquire(bazel: list[str], flags: list[str], root: Path, destination: Path) -> None:
    def query(label: str) -> list[Path]:
        output = subprocess.check_output(
            [*bazel, "cquery", label, "--output=files", *flags],
            cwd=root,
            text=True,
        )
        return [execution / line for line in output.splitlines()]

    os_name = {"Darwin": "darwin", "Linux": "linux"}[platform.system()]
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}[platform.machine()]
    runtimes = {"bun": "//:bun", "node": f"//:node_{os_name}_{arch}"}
    subprocess.run(
        [*bazel, "build", "@npm//:files", *runtimes.values(), *flags],
        cwd=root,
        check=True,
    )
    execution = Path(
        subprocess.check_output(
            [*bazel, "info", "execution_root", *flags],
            cwd=root,
            text=True,
        ).strip()
    )
    (manifest,) = query("@npm//:closure.json")
    repository = manifest.parent
    # Copy only the public filegroup: incidental repository files are not inputs.
    for source in query("@npm//:files"):
        target = destination / source.relative_to(repository)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for name, label in runtimes.items():
        (source,) = query(label)
        shutil.copy2(source, destination / name)
    shutil.copy2(root / "demo.mjs", destination / "demo.mjs")
    print(f"Exported consumer to {destination}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", default="bazel")
    parser.add_argument("--output-user-root", type=Path)
    parser.add_argument(
        "--registry", help="Test registry used with dev dependencies disabled"
    )
    parser.add_argument("--export", type=Path, help="Export without running the demo")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    bazel = [args.bazel, "--ignore_all_rc_files"]
    if args.output_user_root:
        bazel.append(f"--output_user_root={args.output_user_root.resolve()}")
    flags = []
    if args.registry:
        flags = [
            "--ignore_dev_dependency",
            f"--registry={args.registry}",
            "--registry=https://bcr.bazel.build",
        ]
    if args.export:
        destination = args.export.resolve()
        destination.mkdir(parents=True, exist_ok=False)
        acquire(bazel, flags, root, destination)
    else:
        with tempfile.TemporaryDirectory(prefix="bun-demo-") as temporary:
            destination = Path(temporary)
            acquire(bazel, flags, root, destination)
            subprocess.run(
                [str(destination / "node"), str(destination / "demo.mjs")],
                cwd=destination,
                check=True,
            )


if __name__ == "__main__":
    main()
