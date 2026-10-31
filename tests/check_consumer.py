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

# The npm rules' dependency outside the Bazel Central Registry, which the
# local registry serves as this module's own build resolves it.
_ARCHIVE_STORE = "curlewlabs_rules_archive_store"


def remove_tree(path: Path) -> None:
    """Remove `path`, whose Bazel outputs include read-only directories, the
    npm rules' per-file units among them."""
    for directory in [
        path,
        *(
            entry
            for entry in path.rglob("*")
            if entry.is_dir() and not entry.is_symlink()
        ),
    ]:
        directory.chmod(directory.stat().st_mode | 0o700)
    shutil.rmtree(path)


def register_dependency(bazel: str, root: Path, registry: Path, name: str) -> None:
    """Serve `name` from the local registry, from the release this module's
    MODULE.bazel overrides it with: its version and MODULE.bazel as Bazel
    resolved them, and its archive's URL, integrity and strip prefix."""
    command = [bazel, "--ignore_all_rc_files"]
    graph = json.loads(
        subprocess.check_output(
            [*command, "mod", "graph", "--output=json"], cwd=root, text=True
        )
    )
    pending = [graph]
    version = None
    while pending and version is None:
        node = pending.pop()
        if node.get("name") == name:
            version = node["version"]
        pending.extend(node.get("dependencies", []))
    if version is None:
        raise RuntimeError(f"{name} is not in this module's dependency graph")
    (definition,) = [
        json.loads(line)
        for line in subprocess.check_output(
            [*command, "mod", "show_repo", f"@{name}", "--output=streamed_jsonproto"],
            cwd=root,
            text=True,
        ).splitlines()
        if line
    ]
    attributes = {
        attribute["name"]: attribute
        for attribute in definition["attribute"]
        if attribute.get("explicitlySpecified")
    }
    subprocess.run(
        [*command, "fetch", f"--repo=@{name}"], cwd=root, check=True, stdout=sys.stderr
    )
    output_base = Path(
        subprocess.check_output(
            [*command, "info", "output_base"], cwd=root, text=True
        ).strip()
    )
    module = registry / "modules" / name / version
    module.mkdir(parents=True)
    shutil.copyfile(
        output_base / "external" / definition["canonicalName"] / "MODULE.bazel",
        module / "MODULE.bazel",
    )
    (module / "source.json").write_text(
        json.dumps(
            {
                "url": attributes["urls"]["stringListValue"][0],
                "integrity": attributes["integrity"]["stringValue"],
                "strip_prefix": attributes["strip_prefix"]["stringValue"],
            }
        )
    )


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
        module = registry / "modules/curlewlabs_rules_bun/0.8.0"
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
        register_dependency(args.bazel, root, registry, _ARCHIVE_STORE)
        npm_consumer = producer / "npm_consumer"
        shutil.copytree(
            root / "tests/npm_consumer",
            npm_consumer,
            ignore=shutil.ignore_patterns("bazel-*", "MODULE.bazel.lock"),
        )
        output = producer / "bazel"
        exported = staging / "exported"
        try:
            # The npm rules as a dependency rather than the root module: their
            # labels, the hub they generate, and rules_archive_store resolved
            # from the registry.
            subprocess.run(
                [
                    args.bazel,
                    "--ignore_all_rc_files",
                    f"--output_user_root={output}",
                    "test",
                    "//...",
                    "--ignore_dev_dependency",
                    f"--registry={registry.as_uri()}",
                    "--registry=https://bcr.bazel.build",
                    "--@curlewlabs_rules_bun//npm:bun=//:bun",
                    "--test_output=errors",
                ],
                cwd=npm_consumer,
                check=True,
            )
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
            for module_root in (npm_consumer, consumer):
                subprocess.run(
                    [
                        args.bazel,
                        "--ignore_all_rc_files",
                        f"--output_user_root={output}",
                        "shutdown",
                    ],
                    cwd=module_root,
                    check=True,
                )
        remove_tree(producer)
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
