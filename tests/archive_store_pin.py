"""The release of rules_archive_store this module's MODULE.bazel overrides, as Bazel resolves it.

The npm rules depend on rules_archive_store, which is not in the Bazel Central
Registry, and an override binds only the root module. So a check whose root is
another module either overrides it there (override) or serves it from a local
registry (register), from this module's own pin rather than a copy of it.
"""

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ARCHIVE_STORE = "curlewlabs_rules_archive_store"


@dataclass(frozen=True)
class Pin:
    name: str
    version: str
    url: str
    integrity: str
    strip_prefix: str
    canonical_name: str


def resolve(bazel: str, root: Path, name: str) -> Pin:
    """`name` as this module's build resolves it: its version from the module
    graph, and its archive from the repository its override defines."""
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
    return Pin(
        name=name,
        version=version,
        url=attributes["urls"]["stringListValue"][0],
        integrity=attributes["integrity"]["stringValue"],
        strip_prefix=attributes["strip_prefix"]["stringValue"],
        canonical_name=definition["canonicalName"],
    )


def override(bazel: str, root: Path, name: str) -> str:
    """An archive_override of `name` for another root module's MODULE.bazel."""
    pin = resolve(bazel, root, name)
    return (
        "archive_override(\n"
        f"    module_name = {json.dumps(pin.name)},\n"
        f"    integrity = {json.dumps(pin.integrity)},\n"
        f"    strip_prefix = {json.dumps(pin.strip_prefix)},\n"
        f"    urls = [{json.dumps(pin.url)}],\n"
        ")\n"
    )


def register(bazel: str, root: Path, registry: Path, name: str) -> None:
    """Serve `name` from the local registry at `registry`, with the
    MODULE.bazel of the release this module overrides it with."""
    pin = resolve(bazel, root, name)
    command = [bazel, "--ignore_all_rc_files"]
    subprocess.run(
        [*command, "fetch", f"--repo=@{name}"], cwd=root, check=True, stdout=sys.stderr
    )
    output_base = Path(
        subprocess.check_output(
            [*command, "info", "output_base"], cwd=root, text=True
        ).strip()
    )
    module = registry / "modules" / name / pin.version
    module.mkdir(parents=True)
    shutil.copyfile(
        output_base / "external" / pin.canonical_name / "MODULE.bazel",
        module / "MODULE.bazel",
    )
    (module / "source.json").write_text(
        json.dumps(
            {
                "url": pin.url,
                "integrity": pin.integrity,
                "strip_prefix": pin.strip_prefix,
            }
        )
    )
