"""Report the package the linked tree at `$ARCHIVE_MOUNT_ROOT/tree` holds.

tree_binary_test runs this through npm_tree_binary's launcher, as
`bazel run` would, to see the tree arrive and the arguments pass through.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main(arguments: list[str]) -> int:
    if not arguments:
        print("tree_probe: no arguments", file=sys.stderr)
        return 2
    manifest = (
        Path(os.environ["ARCHIVE_MOUNT_ROOT"]) / "tree/node_modules/semver/package.json"
    )
    name = json.loads(manifest.read_text(encoding="utf-8"))["name"]
    print(f"tree_probe: read {name} for {', '.join(arguments)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
