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
    module = json.loads(
        subprocess.check_output(
            [*command, "mod", "graph", "--output=json", "--depth=1"],
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
    subprocess.run(
        [*command, "build", "//docs:api"], cwd=root, check=True, stdout=sys.stderr
    )
    execution = Path(
        subprocess.check_output(
            [*command, "info", "execution_root"], cwd=root, text=True
        ).strip()
    )
    doc_path = subprocess.check_output(
        [*command, "cquery", "//docs:api", "--output=files"], cwd=root, text=True
    ).strip()
    documentation = (execution / doc_path).read_bytes()
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
            if member.name == f"{prefix}/docs/api.md":
                raise ValueError("Generated API documentation must not be tracked")
            member.mtime = 0
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            member.pax_headers = {}
            packaged.addfile(
                member,
                committed.extractfile(member) if member.isfile() else None,
            )
        api = tarfile.TarInfo(f"{prefix}/docs/api.md")
        api.size = len(documentation)
        api.mode = 0o644
        packaged.addfile(api, io.BytesIO(documentation))
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

The archive includes generated API documentation at `docs/api.md`, the runnable
independent consumer, and license notices. Runtimes and npm packages are
separate downloads with their own licenses. Acquisition is unsandboxed;
consumers supply their own execution isolation. This release is not a BCR entry.
""")


if __name__ == "__main__":
    main()
