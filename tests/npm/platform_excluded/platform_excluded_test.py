"""The hand-written graph's linked tree, as each platform's Bun would install it.

Bun's walk stops at a node the platform excludes, so a tree holds nothing the
excluded node alone reaches, and a `.bin` name two dependencies declare goes to
the one whose name sorts first among those the platform installs. The graph
(npm_graph.json, beside this file) puts each shape in front of the hub:

- `a-tool`, semver 7.8.5, Linux on x64 only, reaches balanced-match (hoisted)
  alone, and acorn-jsx, which acorn reaches too.
- `b-tool`, semver 6.3.1, macOS only, reaches brace-expansion alone.
- `c-tool`, acorn, on every platform. All three declare the bin `tool`.
- `d-cycle`, deep-is, in a cycle with escape-string-regexp, Linux only, which
  alone reaches @keyv/serialize, the cycle's third member. `e-cycle-linux`
  links escape-string-regexp itself.

The test reads the tree npm_tree_test gives it, in whichever representation
the build chose, and runs on each platform CI covers.
"""

from __future__ import annotations

import os
import platform
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import override


@dataclass(frozen=True)
class _Tree:
    """What one platform's tree holds."""

    # Store directories, each a node's id with its `/` written `+`.
    store: frozenset[str]
    # The root workspace's links, `.bin` aside.
    roots: frozenset[str]
    # The names linked in the store's fallback directory.
    hoisted: frozenset[str]
    # The root link whose package `.bin/tool` lands in.
    tool: str


_SHARED = frozenset({"acorn@8.19.0", "acorn-jsx@5.3.2", "deep-is@0.1.4"})
_LINUX_CYCLE = frozenset({"escape-string-regexp@4.0.0", "@keyv+serialize@1.1.1"})
_MACOS = _Tree(
    store=_SHARED | {"semver@6.3.1", "brace-expansion@5.0.12"},
    roots=frozenset({"b-tool", "c-tool", "d-cycle"}),
    hoisted=frozenset(),
    tool="b-tool",
)
_EXPECTED = {
    ("linux", "x64"): _Tree(
        store=_SHARED | _LINUX_CYCLE | {"semver@7.8.5", "balanced-match@4.0.4"},
        roots=frozenset({"a-tool", "c-tool", "d-cycle", "e-cycle-linux"}),
        hoisted=frozenset({"balanced-match"}),
        tool="a-tool",
    ),
    ("linux", "arm64"): _Tree(
        store=_SHARED | _LINUX_CYCLE,
        roots=frozenset({"c-tool", "d-cycle", "e-cycle-linux"}),
        hoisted=frozenset(),
        tool="c-tool",
    ),
    ("darwin", "arm64"): _MACOS,
    ("darwin", "x64"): _MACOS,
}

_CPU = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}


class PlatformExcludedTreeTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.root = Path(os.environ["ARCHIVE_MOUNT_ROOT"], "tree")
        host = (sys.platform, _CPU[platform.machine().lower()])
        self.expected = _EXPECTED[host]

    def entries(self, directory: Path) -> set[str]:
        # No name the graph links here is scoped, and a store directory
        # writes a scope's `/` as `+`, so one level lists them all.
        if not directory.is_dir():
            return set()
        return {entry.name for entry in directory.iterdir()}

    def test_the_store_holds_only_what_admitted_nodes_reach(self) -> None:
        # A node reached only through an excluded one is not installed: an
        # excluded node's own descendants, and a cycle member reached only
        # through an excluded member.
        store = self.entries(self.root / ".store") - {"node_modules"}
        self.assertEqual(store, self.expected.store)

    def test_the_root_links_only_admitted_dependencies(self) -> None:
        roots = self.entries(self.root / "node_modules") - {".bin"}
        self.assertEqual(roots, self.expected.roots)

    def test_the_fallback_holds_no_node_an_excluded_node_alone_reaches(
        self,
    ) -> None:
        # balanced-match is hoisted, and is reached only through a-tool.
        hoisted = self.entries(self.root / ".store/node_modules")
        self.assertEqual(hoisted, self.expected.hoisted)

    def test_a_shared_bin_goes_to_the_first_dependency_the_platform_admits(
        self,
    ) -> None:
        # a-tool, b-tool and c-tool each declare `tool`, and a-tool, first by
        # name, is excluded everywhere but Linux x64: an owner chosen without
        # regard to the platform leaves every other platform without the bin.
        self.assertEqual(self.entries(self.root / "node_modules/.bin"), {"tool"})
        owner = _in_store(self.root / "node_modules" / self.expected.tool)
        tool = _in_store(self.root / "node_modules/.bin/tool")
        self.assertTrue(tool.startswith(owner + "/"), f"{tool} is not in {owner}")
        self.assertTrue(os.access(self.root / "node_modules/.bin/tool", os.X_OK))


def _in_store(path: Path) -> str:
    """Where `path` lands from its tree's `.store`.

    Per-file, a runfiles tree links each file to its output, so a file and the
    directory holding it can resolve under different roots.
    """
    resolved = path.resolve().as_posix()
    if "/.store/" not in resolved:
        raise AssertionError(f"{path} resolves outside the store: {resolved}")
    return resolved.rsplit("/.store/", 1)[1]


if __name__ == "__main__":
    unittest.main()
