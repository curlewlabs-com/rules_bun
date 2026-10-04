"""Verify an independent module against a source archive, then remove its producer."""

import argparse
import base64
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", default="bazel")
    parser.add_argument("--archive", type=Path, help="Test a supplied source archive")
    parser.add_argument("--strip-prefix", default="")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="bun-archive-consumer-") as temporary:
        staging = Path(temporary).resolve()
        producer = staging / "producer"
        producer.mkdir()
        archive = producer / "rules_bun.tar.gz"
        if args.archive:
            shutil.copyfile(args.archive, archive)
        else:
            # git archive excludes caches and uncommitted files from the candidate.
            subprocess.run(
                ["git", "archive", "--format=tar.gz", f"--output={archive}", "HEAD"],
                cwd=root,
                check=True,
            )
        integrity = (
            "sha256-"
            + base64.b64encode(hashlib.sha256(archive.read_bytes()).digest()).decode()
        )
        print(f"Testing source archive: {integrity}", flush=True)
        consumer = producer / "consumer"
        shutil.copytree(
            root / "examples/consumer",
            consumer,
            ignore=shutil.ignore_patterns(
                "bazel-*", "node_modules", "MODULE.bazel.lock", "__pycache__"
            ),
        )
        # Bazel ignores archive_override with --ignore_dev_dependency, so serve the
        # exact archive through a local registry without requiring BCR publication.
        registry = producer / "registry"
        module = registry / "modules/curlewlabs_rules_bun/0.1.0"
        module.mkdir(parents=True)
        with tarfile.open(archive) as packaged:
            member = "/".join(filter(None, [args.strip_prefix, "MODULE.bazel"]))
            module_file = packaged.extractfile(member)
            if module_file is None:
                raise RuntimeError("Source archive lacks MODULE.bazel")
            (module / "MODULE.bazel").write_bytes(module_file.read())
        (module / "source.json").write_text(
            json.dumps(
                {
                    "url": archive.as_uri(),
                    "integrity": integrity,
                    "strip_prefix": args.strip_prefix,
                }
            )
        )
        output = producer / "bazel"
        exported = staging / "exported"
        try:
            subprocess.run(
                [
                    sys.executable,
                    str(consumer / "run.py"),
                    "--bazel",
                    args.bazel,
                    "--registry",
                    registry.as_uri(),
                    "--output-user-root",
                    str(output),
                    "--export",
                    str(exported),
                ],
                check=True,
            )
        finally:
            subprocess.run(
                [
                    args.bazel,
                    "--ignore_all_rc_files",
                    f"--output_user_root={output}",
                    "shutdown",
                ],
                cwd=consumer,
                check=True,
            )
        shutil.rmtree(producer)
        print(
            "Removed source archive, consumer module, and Bazel acquisition state.",
            flush=True,
        )
        command = [str(exported / "node"), str(exported / "demo.mjs")]
        subprocess.run(command, cwd=exported, check=True)

        # The consumer must reject damaged exported bytes before running packages.
        payload = exported / "workspace/lifecycle.json"
        original = payload.read_bytes()
        payload.chmod(0o644)
        payload.write_bytes(b"corrupt payload")
        for diagnostic in ["Payload digest mismatch: lifecycle.json", "ENOENT"]:
            result = subprocess.run(
                command, cwd=exported, text=True, capture_output=True, check=False
            )
            if result.returncode == 0 or diagnostic not in result.stderr:
                raise RuntimeError(
                    f"Expected {diagnostic}: {result.stdout}{result.stderr}"
                )
            print(f"Verified damaged payload rejection: {diagnostic}", flush=True)
            if payload.exists():
                payload.unlink()
        payload.write_bytes(original)
        subprocess.run(command, cwd=exported, check=True)


if __name__ == "__main__":
    main()
