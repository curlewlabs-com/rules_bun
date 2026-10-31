"""A bin runs from the tree npm_tree_test gives, in a module that depends on rules_bun."""

import os
import subprocess
import sys
from pathlib import Path

bun = Path(sys.argv[1]).resolve()
result = subprocess.run(
    [str(bun), "--no-install", "node_modules/.bin/semver", "1.2.3", "-r", ">=1"],
    cwd=Path(os.environ["ARCHIVE_MOUNT_ROOT"], "tree"),
    env={"PATH": "", "HOME": os.environ["TEST_TMPDIR"]},
    capture_output=True,
    text=True,
    check=False,
    timeout=120,
)
if result.returncode != 0 or result.stdout.strip() != "1.2.3":
    sys.exit(f"semver did not run from the tree: {result.stdout}{result.stderr}")
