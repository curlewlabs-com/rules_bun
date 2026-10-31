"""Prepare and exercise a source-only release candidate; print release notes."""

import argparse
import base64
import gzip
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", nargs="?", help="Optional vMAJOR.MINOR.PATCH to verify")
    parser.add_argument("--bazel", default="bazel")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    # Bazel refreshes platform-specific lock entries; the archive uses HEAD's lock.
    subprocess.run(
        [
            "git",
            "diff",
            "--exit-code",
            "HEAD",
            "--",
            ".",
            ":(top,exclude)MODULE.bazel.lock",
        ],
        cwd=root,
        check=True,
        stdout=sys.stderr,
    )
    command = [args.bazel, "--ignore_all_rc_files"]
    # Root metadata does not require evaluating the development extensions.
    module = json.loads(
        subprocess.check_output(
            [
                *command,
                "mod",
                "graph",
                "--output=json",
                "--depth=1",
                "--ignore_dev_dependency",
                "--lockfile_mode=off",
            ],
            cwd=root,
            text=True,
        )
    )
    version = module["version"]
    tag = args.tag or f"v{version}"
    if (
        module["name"] != "curlewlabs_rules_bun"
        or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag)
        or tag != f"v{version}"
    ):
        raise ValueError(
            f"Release tag {tag!r} does not match the module: {module['name']}@{version}"
        )
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    # Each generated API document, by its target, at its path in the archive.
    documents = {"//docs:api": "docs/api.md", "//docs:npm_api": "docs/npm-api.md"}
    subprocess.run(
        [*command, "build", *documents], cwd=root, check=True, stdout=sys.stderr
    )
    execution = Path(
        subprocess.check_output(
            [*command, "info", "execution_root"], cwd=root, text=True
        ).strip()
    )
    documentation = {
        path: (
            execution
            / subprocess.check_output(
                [*command, "cquery", target, "--output=files"], cwd=root, text=True
            ).strip()
        ).read_bytes()
        for target, path in documents.items()
    }
    prefix = f"rules_bun-{version}"
    output = root / "dist"
    output.mkdir(exist_ok=True)
    archive = output / f"{prefix}.tar.gz"
    source = subprocess.check_output(
        ["git", "archive", "--format=tar", f"--prefix={prefix}/", "HEAD"], cwd=root
    )
    # Normalize Git's commit timestamp and PAX comment, keeping tracked file modes.
    with (
        tarfile.open(fileobj=io.BytesIO(source)) as committed,
        archive.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as packaged,
    ):
        for member in committed.getmembers():
            if member.name.removeprefix(f"{prefix}/") in documentation:
                raise ValueError("Generated API documentation must not be tracked")
            member.mtime = 0
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            member.pax_headers = {}
            packaged.addfile(
                member,
                committed.extractfile(member) if member.isfile() else None,
            )
        for path, content in documentation.items():
            api = tarfile.TarInfo(f"{prefix}/{path}")
            api.size = len(content)
            api.mode = 0o644
            packaged.addfile(api, io.BytesIO(content))
    digest = hashlib.sha256(archive.read_bytes()).digest()
    integrity = "sha256-" + base64.b64encode(digest).decode()
    (output / "SHA256SUMS").write_text(f"{digest.hex()}  {archive.name}\n")
    (output / "source.json").write_text(
        json.dumps(
            {
                "module": module["name"],
                "version": version,
                "commit": revision,
                "archive": archive.name,
                "integrity": integrity,
                "strip_prefix": prefix,
            },
            indent=2,
        )
        + "\n"
    )
    print(
        f"Prepared {archive.name}: {archive.stat().st_size} bytes; {integrity}",
        file=sys.stderr,
        flush=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(root / "tests/check_consumer.py"),
            "--bazel",
            args.bazel,
            "--archive",
            str(archive),
            "--strip-prefix",
            prefix,
        ],
        cwd=root,
        check=True,
        stdout=sys.stderr,
    )
    print(f"""Source commit: `{revision}`

Install the verified source archive using Bzlmod:

```starlark
bazel_dep(name = "curlewlabs_rules_bun", version = "{version}")
archive_override(
    module_name = "curlewlabs_rules_bun",
    urls = ["https://github.com/curlewlabs-com/rules_bun/releases/download/{tag}/{archive.name}"],
    integrity = "{integrity}",
    strip_prefix = "{prefix}",
)
```

The npm rules depend on `curlewlabs_rules_archive_store`, which is not a BCR
entry either, so a module using them also overrides that module
(`docs/npm.md`, "Setup").

The archive includes generated API documentation at `docs/api.md` and
`docs/npm-api.md`, the runnable independent consumer, and license notices. Runtimes and npm packages are
separate downloads with their own licenses. Acquisition is unsandboxed;
consumers supply their own execution isolation. This release is not a BCR entry.
""")


if __name__ == "__main__":
    main()
