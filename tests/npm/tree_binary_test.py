"""npm_tree_binary's launcher must give the tree, keep the binary's answer and leave nothing behind.

Under `bazel run` there is no test directory to hold the tree's root, so the
launcher makes one of its own; a root left behind would pile up in the
caller's temporary directory with every run, and a lost exit status would let
a failed run pass for a good one.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

# A ceiling for one launcher run, well above what one takes, so a hung
# launcher fails in the case that started it.
_RUN_SECONDS = 120

_launcher: Path


class NpmTreeBinaryTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.temporary = Path(tempfile.mkdtemp(dir=os.environ.get("TEST_TMPDIR")))

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = {
            name: value
            for name, value in os.environ.items()
            # What `bazel run` lacks, and what would point the launcher at a
            # store or root other than its own.
            if name
            not in {
                "TEST_TMPDIR",
                "ARCHIVE_MOUNT_ROOT",
                "RULES_ARCHIVE_STORE_DIR",
                "RUNNER_TEMP",
            }
        }
        environment["TMPDIR"] = str(self.temporary)
        return subprocess.run(
            [str(_launcher), *arguments],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
            timeout=_RUN_SECONDS,
        )

    def test_gives_the_tree_and_forwards_arguments_then_removes_its_root(
        self,
    ) -> None:
        result = self._run("tar", "zip")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("tree_probe: read semver for tar, zip", result.stdout)
        # The root, and on macOS the helper's private store, were made here.
        self.assertEqual(list(self.temporary.iterdir()), [])

    def test_keeps_a_failed_binarys_status_and_still_removes_its_root(self) -> None:
        # tree_probe refuses to run with no arguments, exiting 2.
        result = self._run()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(list(self.temporary.iterdir()), [])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--launcher", required=True, type=Path)
    parsed, remaining = parser.parse_known_args()
    _launcher = parsed.launcher.absolute()
    unittest.main(argv=[sys.argv[0], *remaining])
