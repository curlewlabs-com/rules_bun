"""Tools run from the fixture's linked tree, as a consumer gets it.

npm_tree_test gives the tree at `$ARCHIVE_MOUNT_ROOT/tree`: the declared links
and units where units are per-file, the tree the archive store published where
they are archives. Bun resolves a package's dependencies from its realpath, so
a tree whose links arrived as copies, whose units were links out of the tree,
or whose edges were missing would fail to resolve here (docs/npm.md
sec."What a layout must keep").
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import unittest
from pathlib import Path
from typing import override

# A ceiling for one tool run, well above what any takes, so a hung tool fails
# in the case that started it rather than at the test's timeout.
_RUN_SECONDS = 120

_ARGUMENTS = argparse.Namespace()


class TreeToolTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.bun = Path(_ARGUMENTS.bun).resolve()
        self.root = Path(os.environ["ARCHIVE_MOUNT_ROOT"], "tree")
        self.scratch = Path(os.environ["TEST_TMPDIR"])

    def run_bun(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        # No PATH: a bin that reached for a host node or tool fails rather
        # than passing on the host's copy.
        return subprocess.run(
            [str(self.bun), "--no-install", *arguments],
            cwd=self.root,
            env={
                "PATH": "",
                "HOME": str(self.scratch),
                "BUN_RUNTIME_TRANSPILER_CACHE_PATH": "0",
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=_RUN_SECONDS,
        )

    def assert_ran(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, f"{result.stdout}\n{result.stderr}")

    def test_units_are_real_directories_inside_the_tree(self) -> None:
        # A unit linked to a directory outside the tree finds none of its
        # dependencies beside its realpath.
        store = (self.root / ".store").resolve()
        unit = (self.root / "node_modules/eslint").resolve()
        self.assertTrue(unit.is_relative_to(store), unit)

    def test_a_bin_is_executable(self) -> None:
        # A tool started by path, as package scripts start theirs, needs the
        # execute bit whatever mode the tarball gave its file.
        semver = (self.root / "node_modules/.bin/semver").resolve()
        self.assertTrue(os.access(semver, os.X_OK), semver)

    def test_a_bin_runs(self) -> None:
        result = self.run_bun("node_modules/.bin/semver", "1.2.3", "-r", ">=1")
        self.assert_ran(result)
        self.assertEqual(result.stdout.strip(), "1.2.3")

    def test_a_tool_in_a_dependency_cycle_resolves_its_dependencies(self) -> None:
        # eslint and @eslint-community/eslint-utils depend on each other, so
        # their targets are one cycle group; every edge must still be linked.
        result = self.run_bun("node_modules/.bin/eslint", "--version")
        self.assert_ran(result)
        self.assertRegex(result.stdout.strip(), r"^v\d+\.\d+\.\d+$")

    def test_a_tarball_url_release_resolves_its_dependencies(self) -> None:
        # A dependency named by its tarball's URL is a release like any other:
        # a unit in the store, with edges to the registry releases it needs.
        script = self.scratch / "visitor_keys.cjs"
        script.write_text(
            "const { createRequire } = require('node:module');\n"
            f"const fromRoot = createRequire({json.dumps(str(self.root / 'resolve.cjs'))});\n"
            "const { visitorKeys } = fromRoot('@typescript-eslint/visitor-keys');\n"
            "console.log(Array.isArray(visitorKeys.Program));\n"
        )
        result = self.run_bun(str(script))
        self.assert_ran(result)
        self.assertEqual(result.stdout.strip(), "true")

    def test_esbuild_finds_its_platform_package(self) -> None:
        # The platform package is an edge only where its os and cpu allow.
        result = self.run_bun("node_modules/.bin/esbuild", "--version")
        self.assert_ran(result)
        self.assertRegex(result.stdout.strip(), r"^\d+\.\d+\.\d+$")

    def test_oxc_parser_links_only_the_bindings_its_platform_admits(
        self,
    ) -> None:
        # Bun installs one Linux binding per cpu, the glibc one, because its
        # own build is glibc; a tree that linked the musl binding too, or a
        # Linux binding on macOS, holds packages Bun's install leaves out.
        cpu = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64"}[
            platform.machine().lower()
        ]
        bindings = [
            f"@oxc-parser/binding-{name}"
            for name in (
                f"darwin-{cpu}",
                f"linux-{cpu}-gnu",
                f"linux-{cpu}-musl",
            )
        ]
        admitted = {bindings[0]} if sys.platform == "darwin" else {bindings[1]}
        parser = (self.root / "node_modules/oxc-parser").resolve()
        script = self.scratch / "oxc.mjs"
        script.write_text(
            "import { createRequire } from 'node:module';\n"
            f"const fromRoot = createRequire({json.dumps(str(self.root / 'resolve.cjs'))});\n"
            "const { parseSync } = await import(fromRoot.resolve('oxc-parser'));\n"
            f"const fromParser = createRequire({json.dumps(str(parser / 'package.json'))});\n"
            f"const found = {json.dumps(bindings)}.filter((name) => {{\n"
            "  try { fromParser.resolve(name); return true; } catch { return false; }\n"
            "});\n"
            "const body = parseSync('x.js', 'let a = 1;').program.body.length;\n"
            "console.log(JSON.stringify({ found, body }));\n"
        )
        result = self.run_bun(str(script))
        self.assert_ran(result)
        reported = json.loads(result.stdout)
        self.assertEqual(set(reported["found"]), admitted)
        # The admitted binding is the one the parser loads.
        self.assertEqual(reported["body"], 1)

    def test_an_alias_links_its_own_release(self) -> None:
        manifest = self.root / "node_modules/semver-six/package.json"
        self.assertEqual(json.loads(manifest.read_text())["version"], "6.3.1")

    def test_a_workspace_links_its_own_copy(self) -> None:
        # The tool workspace pins a version the root does not, which Bun
        # keys under the workspace's own name in the lock.
        manifest = self.root / "packages/tool/node_modules/semver/package.json"
        self.assertEqual(json.loads(manifest.read_text())["version"], "6.3.1")

    def test_an_undeclared_dependency_resolves_through_the_hoisted_fallback(
        self,
    ) -> None:
        # esbuild does not declare semver; under Bun's isolated install a
        # package's lookup reaches the copy hoisted into the store's fallback
        # directory after its own edges, and a tree without that link fails
        # with "Cannot find module 'semver'".
        esbuild = (self.root / "node_modules/esbuild").resolve()
        script = self.scratch / "resolve.cjs"
        script.write_text(
            "const { createRequire } = require('node:module');\n"
            f"const fromEsbuild = createRequire({json.dumps(str(esbuild / 'package.json'))});\n"
            "console.log(fromEsbuild.resolve('semver'));\n"
        )
        result = self.run_bun(str(script))
        self.assert_ran(result)
        self.assertIn("/.store/", result.stdout, result.stderr)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bun", required=True, help="the Bun's rootpath")
    _, rest = parser.parse_known_args(namespace=_ARGUMENTS)
    unittest.main(argv=[sys.argv[0], *rest])


if __name__ == "__main__":
    main()
