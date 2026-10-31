"""Check that the graph comparison fails on the mistakes it exists to catch.

npm_bun_install_test compares the real graph with Bun's real install, where a
passing comparison is the common result; these cases guard the other side, a
comparison that passes when the graph is wrong. A peer variant linked from the
wrong dependent, an edge retargeted to another release, and a dependency cycle
are the shapes the translator's model must get right
(docs/npm.md sec."Translation").
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from npm_bun_install import (
    Graph,
    Store,
    StoreDirectory,
    compare,
    read_graph,
    read_store,
)


def _graph(
    packages: dict[str, dict[str, object]],
    roots: dict[str, str],
    hoisted: dict[str, str] | None = None,
) -> Graph:
    releases = {str(record["repository"]) for record in packages.values()}
    return read_graph(
        {
            "repositories": {
                repository: {
                    "name": repository.rsplit("@", 1)[0],
                    "version": repository.rsplit("@", 1)[1],
                    "url": f"https://registry.npmjs.org/{repository}.tgz",
                }
                for repository in releases
            },
            "packages": packages,
            "workspaces": {".": roots},
            "hoisted": hoisted or {},
        }
    )


# A release split into peer variants: `plugin` links `peer@1` under `app` and
# `peer@2` under `tool`, and `a` and `b` depend on each other.
_PACKAGES: dict[str, dict[str, object]] = {
    "app@1": {"repository": "app@1", "edges": {"plugin": "plugin@1+one", "a": "a@1"}},
    "tool@1": {"repository": "tool@1", "edges": {"plugin": "plugin@1+two"}},
    "plugin@1+one": {"repository": "plugin@1", "edges": {"peer": "peer@1"}},
    "plugin@1+two": {"repository": "plugin@1", "edges": {"peer": "peer@2"}},
    "peer@1": {"repository": "peer@1", "edges": {}},
    "peer@2": {"repository": "peer@2", "edges": {}},
    "a@1": {"repository": "a@1", "edges": {"b": "b@1"}},
    "b@1": {"repository": "b@1", "edges": {"a": "a@1"}},
    "native-linux@1": {"repository": "native-linux@1", "os": ["linux"], "edges": {}},
}
_ROOTS = {"app": "app@1", "tool": "tool@1"}

# Bun's install of the same lock on macOS, under Bun's own directory names.
_STORE = Store(
    directories={
        "app@1": StoreDirectory("app@1", {"plugin": "plugin@1+x", "a": "a@1"}),
        "tool@1": StoreDirectory("tool@1", {"plugin": "plugin@1+y"}),
        "plugin@1+x": StoreDirectory("plugin@1", {"peer": "peer@1"}),
        "plugin@1+y": StoreDirectory("plugin@1", {"peer": "peer@2"}),
        "peer@1": StoreDirectory("peer@1", {}),
        "peer@2": StoreDirectory("peer@2", {}),
        "a@1": StoreDirectory("a@1", {"b": "b@1"}),
        "b@1": StoreDirectory("b@1", {"a": "a@1"}),
    },
    workspaces={".": {"app": "app@1", "tool": "tool@1"}},
)


def _retargeted(
    node: str, dependency: str, target: str
) -> dict[str, dict[str, object]]:
    packages = copy.deepcopy(_PACKAGES)
    edges = packages[node]["edges"]
    assert isinstance(edges, dict)
    edges[dependency] = target
    return packages


class CompareTest(unittest.TestCase):
    def test_matching_install_passes(self) -> None:
        # The Linux-only node is neither installed on macOS nor expected, and
        # the cycle matches only if its members are matched together.
        self.assertEqual(
            compare(_STORE, _graph(_PACKAGES, _ROOTS), "darwin", "arm64"), []
        )

    def test_edge_to_another_release_names_the_node(self) -> None:
        graph = _graph(_retargeted("plugin@1+one", "peer", "peer@2"), _ROOTS)
        report = "\n".join(compare(_STORE, graph, "darwin", "arm64"))
        self.assertIn("Store directory plugin@1+x matches no node of plugin@1", report)
        # With the edge retargeted, the graph's variants of `plugin` are
        # identical, and none links what Bun's `x` directory links.
        self.assertRegex(report, r"peer +peer@1 +peer@2 +peer@2")

    def test_edge_to_the_wrong_peer_variant_names_its_dependent(self) -> None:
        # Every release still agrees; only which variant `app` links differs,
        # which a comparison by release alone would pass.
        graph = _graph(_retargeted("app@1", "plugin", "plugin@1+two"), _ROOTS)
        report = "\n".join(compare(_STORE, graph, "darwin", "arm64"))
        self.assertIn("store directory app@1 (plugin)", report)
        self.assertIn("node app@1 (plugin)", report)

    def test_node_the_platform_installs_but_bun_did_not_fails(self) -> None:
        roots = {**_ROOTS, "native-linux": "native-linux@1"}
        report = "\n".join(compare(_STORE, _graph(_PACKAGES, roots), "linux", "x64"))
        self.assertIn(
            "Node native-linux@1 matches no store directory of native-linux@1: "
            "Bun installs no native-linux@1 here",
            report,
        )

    def test_identical_store_directories_match_one_node(self) -> None:
        # Bun keeps identical trees in separate directories (observed for
        # eslint), so requiring a directory per node would fail a correct
        # graph.
        store = Store(
            directories={
                **_STORE.directories,
                "peer@1+z": StoreDirectory("peer@1", {}),
            },
            workspaces=_STORE.workspaces,
        )
        self.assertEqual(
            compare(store, _graph(_PACKAGES, _ROOTS), "darwin", "arm64"), []
        )

    def test_identical_nodes_the_translator_did_not_merge_fail(self) -> None:
        # The translator merges identical trees into one node, so nodes
        # matching one directory name a merge it missed.
        packages = _retargeted("plugin@1+one", "peer", "peer@1+dup")
        packages["peer@1+dup"] = {"repository": "peer@1", "edges": {}}
        store = Store(
            directories=_STORE.directories,
            workspaces={".": {**_STORE.workspaces["."], "peer": "peer@1"}},
        )
        report = "\n".join(
            compare(
                store, _graph(packages, {**_ROOTS, "peer": "peer@1"}), "darwin", "arm64"
            )
        )
        self.assertIn(
            "Nodes peer@1, peer@1+dup link identical trees, all matching store "
            "directory peer@1",
            report,
        )

    def test_workspace_missing_from_the_graph_fails(self) -> None:
        # The install's own roster decides which workspaces are read, so a
        # workspace the translator left out cannot pass unread.
        store = Store(
            directories=_STORE.directories,
            workspaces={**_STORE.workspaces, "extra": {"peer": "peer@2"}},
        )
        report = compare(store, _graph(_PACKAGES, _ROOTS), "darwin", "arm64")
        self.assertIn(
            "Workspace extra is declared in package.json but missing from the graph",
            report,
        )

    def test_nodes_identical_only_on_this_host_pass(self) -> None:
        # The variants differ only through a Linux-only package, which a macOS
        # host does not install. The translator rightly keeps them apart, so
        # matching one macOS directory is no missed merge.
        packages = copy.deepcopy(_PACKAGES)
        packages["opt@1+linux"] = {
            "repository": "opt@1",
            "edges": {"native-linux": "native-linux@1"},
        }
        packages["opt@1+plain"] = {"repository": "opt@1", "edges": {}}
        roots = {**_ROOTS, "opt-a": "opt@1+linux", "opt-b": "opt@1+plain"}
        store = Store(
            directories={**_STORE.directories, "opt@1": StoreDirectory("opt@1", {})},
            workspaces={
                ".": {**_STORE.workspaces["."], "opt-a": "opt@1", "opt-b": "opt@1"}
            },
        )
        self.assertEqual(compare(store, _graph(packages, roots), "darwin", "arm64"), [])

    def test_nodes_the_translator_keeps_apart_by_metadata_pass(self) -> None:
        # The translator merges on bin and on whether os or cpu is present at
        # all, not only on edges, so nodes differing there alone are no
        # missed merge even when they match one Bun directory.
        for name, extra in (
            ("bin", {"bin": {"opt": "cli.js"}}),
            ("empty os", {"os": []}),
        ):
            with self.subTest(name):
                packages = copy.deepcopy(_PACKAGES)
                packages["opt@1+a"] = {"repository": "opt@1", "edges": {}}
                packages["opt@1+b"] = {"repository": "opt@1", "edges": {}, **extra}
                roots = {**_ROOTS, "opt-a": "opt@1+a", "opt-b": "opt@1+b"}
                store = Store(
                    directories={
                        **_STORE.directories,
                        "opt@1": StoreDirectory("opt@1", {}),
                    },
                    workspaces={
                        ".": {
                            **_STORE.workspaces["."],
                            "opt-a": "opt@1",
                            "opt-b": "opt@1",
                        }
                    },
                )
                self.assertEqual(
                    compare(store, _graph(packages, roots), "darwin", "arm64"), []
                )

    def test_read_store_reads_a_workspace_the_graph_omits(self) -> None:
        # The roster comes from the install's package.json; reading it from
        # the graph would skip the omitted workspace and pass.
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "package.json").write_text(json.dumps({"workspaces": ["extra"]}))
            (root / "node_modules" / ".bun").mkdir(parents=True)
            (root / "extra").mkdir()
            graph = read_graph(
                {
                    "repositories": {},
                    "packages": {},
                    "workspaces": {".": {}},
                    "hoisted": {},
                }
            )
            report = compare(read_store(root, graph), graph, "darwin", "arm64")
        self.assertIn(
            "Workspace extra is declared in package.json but missing from the graph",
            report,
        )

    def test_a_fallback_link_to_another_variant_or_release_fails(self) -> None:
        # The fallback is how a package finds what it requires without
        # declaring it, so a graph naming a different copy there than Bun
        # links would give such a package the wrong one.
        store = Store(
            directories=_STORE.directories,
            workspaces=_STORE.workspaces,
            hoisted={"plugin": "plugin@1+x", "peer": "peer@1"},
        )
        matching = _graph(
            _PACKAGES, _ROOTS, {"plugin": "plugin@1+one", "peer": "peer@1"}
        )
        self.assertEqual(compare(store, matching, "darwin", "arm64"), [])
        report = "\n".join(
            compare(
                store,
                _graph(_PACKAGES, _ROOTS, {"plugin": "plugin@1+two", "peer": "peer@2"}),
                "darwin",
                "arm64",
            )
        )
        self.assertIn("the fallback's plugin", report)
        self.assertIn(
            "The store's fallback links peer to peer@1 under Bun, and to peer@2 in"
            " the graph",
            report,
        )

    def test_a_fallback_link_on_one_side_only_fails(self) -> None:
        # Bun hoisting a name the graph does not, or the reverse, leaves a
        # package's undeclared require resolving on one side only.
        report = compare(
            Store(
                directories=_STORE.directories,
                workspaces=_STORE.workspaces,
                hoisted={"peer": "peer@1"},
            ),
            _graph(_PACKAGES, _ROOTS, {"app": "app@1"}),
            "darwin",
            "arm64",
        )
        self.assertIn(
            "The store's fallback links app to nothing under Bun, and to app@1 in"
            " the graph",
            report,
        )
        self.assertIn(
            "The store's fallback links peer to peer@1 under Bun, and to nothing in"
            " the graph",
            report,
        )


def _bin_store(bins: dict[str, str]) -> Store:
    return Store(
        directories=_STORE.directories,
        workspaces=_STORE.workspaces,
        bins={".": bins},
    )


# `app` and `tool` both name a bin `run`, which the hub gives `app`, the name
# that sorts first; `tool` also names `only-tool`, and `peer` names `peer`.
_BIN_PACKAGES = copy.deepcopy(_PACKAGES)
_BIN_PACKAGES["app@1"]["bin"] = {"run": "app.js"}
_BIN_PACKAGES["tool@1"]["bin"] = {"run": "tool.js", "only-tool": "only.js"}


class BinCompareTest(unittest.TestCase):
    def test_bins_bun_links_as_the_hub_does_pass(self) -> None:
        report = compare(
            _bin_store({"run": "app@1", "only-tool": "tool@1"}),
            _graph(_BIN_PACKAGES, _ROOTS),
            "darwin",
            "arm64",
        )
        self.assertEqual(report, [])

    def test_a_colliding_name_bun_gives_another_dependency_fails(self) -> None:
        # The hub's choice of owner is a model of Bun's, and a Bun that chose
        # otherwise would run another package's tool under the same name.
        report = compare(
            _bin_store({"run": "tool@1", "only-tool": "tool@1"}),
            _graph(_BIN_PACKAGES, _ROOTS),
            "darwin",
            "arm64",
        )
        self.assertIn(
            "Workspace . links .bin/run to tool@1 under Bun, and to app in the npm hub",
            report,
        )

    def test_a_bin_only_the_hub_links_passes(self) -> None:
        # Bun links no bin of a workspace's own copy of a dependency; the hub
        # does, as npm does, which only adds a tool Bun's install lacks.
        report = compare(
            _bin_store({"run": "app@1"}),
            _graph(_BIN_PACKAGES, _ROOTS),
            "darwin",
            "arm64",
        )
        self.assertEqual(report, [])

    def test_a_bin_in_a_dependency_of_the_hubs_package_passes(self) -> None:
        # Bun links esbuild's bin to its platform package's binary, a
        # dependency of the package whose bin script the hub links.
        packages = copy.deepcopy(_BIN_PACKAGES)
        packages["app@1"]["bin"] = {"run": "app.js", "a": "a.js"}
        report = compare(
            _bin_store({"run": "app@1", "a": "a@1", "only-tool": "tool@1"}),
            _graph(packages, _ROOTS),
            "darwin",
            "arm64",
        )
        self.assertEqual(report, [])

    def test_a_bin_bun_links_that_the_hub_does_not_fails(self) -> None:
        report = compare(
            _bin_store({"run": "app@1", "only-tool": "tool@1", "peer": "peer@1"}),
            _graph(_BIN_PACKAGES, _ROOTS),
            "darwin",
            "arm64",
        )
        self.assertIn(
            "Workspace . links .bin/peer to peer@1 under Bun, and to nothing in the"
            " npm hub",
            report,
        )


if __name__ == "__main__":
    unittest.main()
