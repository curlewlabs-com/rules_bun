"""Check the npm graph against Bun's own install of the same lock.

npm_graph.ts models how Bun links packages rather than letting Bun do it: which
peer a package links, and when one release splits into several nodes
(docs/npm.md sec."Translation"). Nothing else notices if a Bun release links
peers differently, or if a lock reaches a shape the model does not cover, so
npm_bun_install_test installs the lock with Bun's isolated linker and compares
Bun's store with the graph, from each side:

- every store directory links, under each dependency name, the same packages as
  some node of its release links under its edges;
- every node this host installs, as reached from the workspaces, matches a
  store directory;
- every workspace package.json declares is in the graph, and links each
  dependency to a store directory matching the node the graph names for it;
- the fallback directory Bun hoists into, `.bun/node_modules`, links each name
  to a store directory matching the node the graph's `hoisted` names;
- no store directory matches several nodes whose trees are identical on every
  platform, which the translator would have failed to merge. Nodes identical
  only on this host may share a directory, as may several directories one
  node: Bun can keep identical trees apart.

Store directories and nodes are matched by structure, not by name: Bun's
directory hash and the graph's node hash are computed differently. A store
directory matches a node when their releases agree, they link the same
dependency names, and each dependency's store directory in turn matches the
node the edge names. That is a greatest fixed point, so a dependency cycle
matches when every member does.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import urllib.parse
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import override

REGISTRY = "https://registry.npmjs.org/"
WORKSPACE = "workspace:"

_NPM_OS = {"darwin": "darwin", "linux": "linux"}
_NPM_CPU = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}


@dataclass(frozen=True)
class Node:
    release: str
    repository: str
    os: tuple[str, ...]
    cpu: tuple[str, ...]
    edges: Mapping[str, str]
    # The translator's merge identity besides edges: its repository, os, cpu
    # and bin, each as the graph holds it, an absent field apart from an
    # empty one (npm_graph.ts `canonical`).
    metadata: str
    # Bin name to its path inside the package.
    bin: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Graph:
    nodes: Mapping[str, Node]
    # Repository name to the registry path Bun requests its tarball at.
    tarballs: Mapping[str, str]
    # Release to the versions locked for its package name, for reading a store
    # directory's name.
    versions: Mapping[str, frozenset[str]]
    workspaces: Mapping[str, Mapping[str, str]]
    # Package name to the node Bun's store hoists into its fallback directory.
    hoisted: Mapping[str, str]


@dataclass(frozen=True)
class StoreDirectory:
    release: str
    # Dependency name to the store directory it links, or `workspace:<dir>`.
    links: Mapping[str, str]


@dataclass(frozen=True)
class Store:
    directories: Mapping[str, StoreDirectory]
    workspaces: Mapping[str, Mapping[str, str]]
    # Package name to the store directory `.bun/node_modules` links it to.
    hoisted: Mapping[str, str] = field(default_factory=dict)
    # Each workspace's `.bin` names, each to the store directory of the file
    # it runs.
    bins: Mapping[str, Mapping[str, str]] = field(default_factory=dict)


def _object(value: object, place: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise TypeError(f"{place}: expected an object")
    return dict(value)


def _string(value: object, place: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{place}: expected a nonempty string")
    return value


def _strings(value: object, place: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TypeError(f"{place}: expected a list")
    return tuple(_string(item, f"{place}[]") for item in value)


def _string_map(value: object, place: str) -> dict[str, str]:
    return {
        key: _string(item, f"{place}.{key}")
        for key, item in _object(value, place).items()
    }


def read_graph(value: object) -> Graph:
    """Narrow the fields this check reads from the npm graph."""
    root = _object(value, "graph")
    releases: dict[str, str] = {}
    tarballs: dict[str, str] = {}
    versions: dict[str, set[str]] = {}
    for name, entry in _object(root.get("repositories"), "repositories").items():
        place = f"repositories.{name}"
        record = _object(entry, place)
        package = _string(record.get("name"), f"{place}.name")
        version = _string(record.get("version"), f"{place}.version")
        url = _string(record.get("url"), f"{place}.url")
        if not url.startswith(REGISTRY):
            raise ValueError(f"{place}.url: not on {REGISTRY}: {url}")
        releases[name] = f"{package}@{version}"
        tarballs[name] = url.removeprefix(REGISTRY)
        versions.setdefault(package, set()).add(version)
    nodes: dict[str, Node] = {}
    for node, entry in _object(root.get("packages"), "packages").items():
        place = f"packages.{node}"
        fields = _object(entry, place)
        repository = _string(fields.get("repository"), f"{place}.repository")
        if repository not in releases:
            raise ValueError(f"{place}.repository: no such repository")
        nodes[node] = Node(
            release=releases[repository],
            repository=repository,
            os=_strings(fields.get("os"), f"{place}.os"),
            cpu=_strings(fields.get("cpu"), f"{place}.cpu"),
            edges=_string_map(fields.get("edges"), f"{place}.edges"),
            metadata=json.dumps(
                [
                    repository,
                    fields.get("os"),
                    fields.get("cpu"),
                    _string_map(fields["bin"], f"{place}.bin")
                    if "bin" in fields
                    else None,
                ],
                sort_keys=True,
            ),
            bin=_string_map(fields.get("bin", {}), f"{place}.bin"),
        )
    workspaces = {
        directory: _string_map(edges, f"workspaces.{directory}")
        for directory, edges in _object(root.get("workspaces"), "workspaces").items()
    }
    hoisted = _string_map(root.get("hoisted"), "hoisted")
    for place, edges in [
        *((f"packages.{n}.edges", node.edges) for n, node in nodes.items()),
        *((f"workspaces.{d}", edges) for d, edges in workspaces.items()),
        ("hoisted", hoisted),
    ]:
        for dependency, target in edges.items():
            if target not in nodes and not target.startswith(WORKSPACE):
                raise ValueError(f"{place}.{dependency}: no such node {target}")
    return Graph(
        nodes=nodes,
        tarballs=tarballs,
        versions={name: frozenset(found) for name, found in versions.items()},
        workspaces=workspaces,
        hoisted=hoisted,
    )


def host_platform() -> tuple[str, str]:
    """npm's os and cpu names for this host."""
    os_name = _NPM_OS.get(sys.platform)
    cpu = _NPM_CPU.get(platform.machine().lower())
    if os_name is None or cpu is None:
        raise RuntimeError(f"No npm platform for {sys.platform}/{platform.machine()}")
    return os_name, cpu


def admits(constraint: Iterable[str], value: str) -> bool:
    """npm's os/cpu rule, as npm_package.bzl applies it.

    A listed negation excludes, and any positive entry makes the list an
    allow-list.
    """
    items = list(constraint)
    if f"!{value}" in items:
        return False
    allowed = [item for item in items if not item.startswith("!")]
    return not allowed or value in allowed


def identical_trees(graph: Graph) -> dict[str, int]:
    """Each node's class of identical linked trees, over the whole graph.

    Nodes share a class when their `metadata` agrees and each dependency name
    links nodes of one class, on every platform: the identity the translator
    merges on. A host's view drops the edges to nodes
    it does not install, which can make nodes the translator rightly keeps
    apart look identical there.
    """

    def target_class(target: str, classes: Mapping[str, int]) -> object:
        return target if target.startswith(WORKSPACE) else classes[target]

    keys = {node: record.metadata for node, record in graph.nodes.items()}
    classes = _number(keys)
    while True:
        refined = _number(
            {
                node: (
                    classes[node],
                    tuple(
                        (dependency, target_class(target, classes))
                        for dependency, target in sorted(record.edges.items())
                    ),
                )
                for node, record in graph.nodes.items()
            }
        )
        # Refinement only splits classes, so an unchanged count is the fixed
        # point.
        if len(set(refined.values())) == len(set(classes.values())):
            return classes
        classes = refined


def _number(keys: Mapping[str, object]) -> dict[str, int]:
    numbers: dict[object, int] = {}
    return {node: numbers.setdefault(key, len(numbers)) for node, key in keys.items()}


def installed_nodes(graph: Graph, os_name: str, cpu: str) -> set[str]:
    """The nodes a host installs: those its platform admits, as reached from
    the workspaces through edges to nodes it admits.
    """

    def admitted(node: str) -> bool:
        record = graph.nodes[node]
        return admits(record.os, os_name) and admits(record.cpu, cpu)

    stack = [
        target
        for edges in graph.workspaces.values()
        for target in edges.values()
        if not target.startswith(WORKSPACE) and admitted(target)
    ]
    reached: set[str] = set()
    while stack:
        node = stack.pop()
        if node in reached:
            continue
        reached.add(node)
        stack.extend(
            target
            for target in graph.nodes[node].edges.values()
            if not target.startswith(WORKSPACE) and admitted(target)
        )
    return reached


def host_tarballs(graph: Graph, os_name: str, cpu: str) -> set[str]:
    """The repositories whose tarballs Bun downloads on this host.

    Bun fetches every locked release the platform admits, reached or not, as
    rules_bun's planner does; os and cpu are a release's, so any node of it
    answers.
    """
    excluded = {
        node.repository
        for node in graph.nodes.values()
        if not (admits(node.os, os_name) and admits(node.cpu, cpu))
    }
    return set(graph.tarballs) - excluded


def _release_of(directory: str, package: str, graph: Graph) -> str:
    # Bun names a store directory `<name>@<version>`, with `/` in a scoped name
    # as `+`, and appends `+<hash>` to a peer variant's. A version can carry
    # `+` build metadata of its own, so the locked versions decide.
    prefix = package.replace("/", "+") + "@"
    if not directory.startswith(prefix):
        raise ValueError(f"Store directory {directory} does not hold {package}")
    rest = directory.removeprefix(prefix)
    found = [
        version
        for version in graph.versions.get(package, ())
        if rest == version or rest.startswith(version + "+")
    ]
    if not found:
        raise ValueError(f"Store directory {directory} names no locked release")
    return f"{package}@{max(found, key=len)}"


def _link_entries(directory: Path) -> Iterable[tuple[str, Path]]:
    """Each name under a node_modules directory, a scope's members by
    `@scope/name`, skipping `.bin` and Bun's own `.bun`.
    """
    for entry in sorted(directory.iterdir()):
        if entry.name in (".bin", ".bun"):
            continue
        if entry.name.startswith("@") and not entry.is_symlink():
            for member in sorted(entry.iterdir()):
                yield f"{entry.name}/{member.name}", member
        else:
            yield entry.name, entry


def declared_workspaces(root: Path) -> list[str]:
    """The workspace directories root's package.json declares, `.` first."""
    manifest = _object(json.loads((root / "package.json").read_text()), "package.json")
    return [".", *_strings(manifest.get("workspaces"), "package.json workspaces")]


def read_store(root: Path, graph: Graph) -> Store:
    """Read Bun's isolated install under root: its store and the links of each
    workspace its package.json declares.
    """
    root = root.resolve()
    store = root / "node_modules" / ".bun"

    def target(link: Path) -> str:
        resolved = link.resolve()
        if resolved.is_relative_to(store):
            return resolved.relative_to(store).parts[0]
        if resolved.is_relative_to(root):
            return WORKSPACE + resolved.relative_to(root).as_posix()
        raise ValueError(f"{link} links outside the install: {resolved}")

    directories: dict[str, StoreDirectory] = {}
    for entry in sorted(store.iterdir()):
        # Bun's fallback links for hoisted lookups, not a package.
        if entry.name == "node_modules":
            continue
        links: dict[str, str] = {}
        own: list[str] = []
        for name, path in _link_entries(entry / "node_modules"):
            if path.is_symlink():
                links[name] = target(path)
            elif path.is_dir():
                own.append(name)
            else:
                raise ValueError(f"Unexpected file in the store: {path}")
        if len(own) != 1:
            raise ValueError(f"Store directory {entry.name} holds packages {own}")
        directories[entry.name] = StoreDirectory(
            release=_release_of(entry.name, own[0], graph), links=links
        )
    for name, directory in directories.items():
        for dependency, linked in directory.links.items():
            if linked not in directories and not linked.startswith(WORKSPACE):
                raise ValueError(f"{name} links {dependency} to no package: {linked}")
    # From the install's own roster, not the graph's, so a workspace the
    # translator left out is still read and then reported.
    workspaces: dict[str, dict[str, str]] = {}
    bins: dict[str, dict[str, str]] = {}
    for workspace in declared_workspaces(root):
        modules = root / workspace / "node_modules"
        links = {}
        if modules.is_dir():
            for name, path in _link_entries(modules):
                if not path.is_symlink():
                    raise ValueError(f"Not a link in a workspace: {path}")
                links[name] = target(path)
        workspaces[workspace] = links
        bins[workspace] = {}
        if (modules / ".bin").is_dir():
            for entry in sorted((modules / ".bin").iterdir()):
                if not entry.is_symlink():
                    raise ValueError(f"Not a link in a workspace's .bin: {entry}")
                bins[workspace][entry.name] = target(entry)
    # A store without the fallback directory hoists nothing.
    fallback = store / "node_modules"
    hoisted = {
        name: target(path)
        for name, path in (_link_entries(fallback) if fallback.is_dir() else ())
        if path.is_symlink()
    }
    return Store(
        directories=directories, workspaces=workspaces, hoisted=hoisted, bins=bins
    )


def expected_bins(graph: Graph, installed: set[str]) -> dict[str, dict[str, str]]:
    """Each workspace's `.bin` names as the npm hub links them: a name two
    dependencies declare goes to the one whose name sorts first."""
    bins: dict[str, dict[str, str]] = {}
    for workspace, edges in graph.workspaces.items():
        owners: dict[str, str] = {}
        for dependency, target in sorted(edges.items()):
            if target in installed:
                for name in sorted(graph.nodes[target].bin):
                    owners.setdefault(name, dependency)
        bins[workspace] = owners
    return bins


def _table(columns: Mapping[str, Mapping[str, str]]) -> list[str]:
    """Columns side by side, one row per dependency any of them links."""
    rows = sorted({row for values in columns.values() for row in values})
    lines = [
        ["dependency", *columns],
        *(
            [row, *(values.get(row, "-") for values in columns.values())]
            for row in rows
        ),
    ]
    widths = [max(len(line[i]) for line in lines) for i in range(len(lines[0]))]
    return [
        "    "
        + "  ".join(c.ljust(w) for c, w in zip(line, widths, strict=True)).rstrip()
        for line in lines
    ]


def compare(store: Store, graph: Graph, os_name: str, cpu: str) -> list[str]:
    """Each mismatch between Bun's install and the graph, as report lines."""
    installed = installed_nodes(graph, os_name, cpu)
    edges = {
        node: {
            dependency: target
            for dependency, target in graph.nodes[node].edges.items()
            if target in installed or target.startswith(WORKSPACE)
        }
        for node in installed
    }
    by_release: dict[str, set[str]] = {}
    for node in installed:
        by_release.setdefault(graph.nodes[node].release, set()).add(node)

    def node_release(target: str) -> str:
        return target if target.startswith(WORKSPACE) else graph.nodes[target].release

    def store_release(target: str) -> str:
        if target.startswith(WORKSPACE):
            return target
        return store.directories[target].release

    def same_releases(directory: StoreDirectory, node: str) -> bool:
        return edges[node].keys() == directory.links.keys() and all(
            node_release(edges[node][dependency]) == store_release(target)
            for dependency, target in directory.links.items()
        )

    # The release-level match: what this directory and a node link, by name.
    direct = {
        name: {
            node
            for node in by_release.get(directory.release, ())
            if same_releases(directory, node)
        }
        for name, directory in store.directories.items()
    }
    matches = {name: set(nodes) for name, nodes in direct.items()}

    def follows(link: str, edge: str) -> bool:
        return link == edge if link.startswith(WORKSPACE) else edge in matches[link]

    changed = True
    while changed:
        changed = False
        for name, directory in store.directories.items():
            kept = {
                node
                for node in matches[name]
                if all(
                    follows(target, edges[node][dependency])
                    for dependency, target in directory.links.items()
                )
            }
            if kept != matches[name]:
                matches[name] = kept
                changed = True

    def unfollowed(directory: StoreDirectory, node: str) -> list[str]:
        return [
            dependency
            for dependency, target in sorted(directory.links.items())
            if not follows(target, edges[node][dependency])
        ]

    # A directory or node that matches only by release links a mismatch
    # reported in full, so each is named once after those, with the
    # dependencies through which it does not match.
    mismatches: list[str] = []
    through: list[str] = []
    for name, directory in sorted(store.directories.items()):
        if matches[name]:
            continue
        if direct[name]:
            failing = {d for n in direct[name] for d in unfollowed(directory, n)}
            through.append(f"store directory {name} ({', '.join(sorted(failing))})")
            continue
        siblings = sorted(by_release.get(directory.release, ()))
        mismatches.append(
            f"Store directory {name} matches no node of {directory.release}"
            + ("" if siblings else ": the graph installs none here")
        )
        columns = {
            f"Bun {name}": {d: store_release(t) for d, t in directory.links.items()}
        }
        for node in siblings:
            columns[f"graph {node}"] = {
                d: node_release(t) for d, t in edges[node].items()
            }
        mismatches.extend(_table(columns))

    # Bun can keep identical trees in separate directories, observed for
    # eslint, so several directories may match one node. The translator
    # merges identical trees into one node, so a directory matching several
    # nodes that are identical across every platform names a merge it missed.
    # Nodes identical only on this host are the translator's to keep apart.
    trees = identical_trees(graph)
    for name, nodes in sorted(matches.items()):
        by_tree: dict[int, list[str]] = {}
        for node in nodes:
            by_tree.setdefault(trees[node], []).append(node)
        for same in by_tree.values():
            if len(same) > 1:
                mismatches.append(
                    f"Nodes {', '.join(sorted(same))} link identical trees, all "
                    f"matching store directory {name}: the translator should "
                    "merge them"
                )

    matched = {node for nodes in matches.values() for node in nodes}
    by_store_release: dict[str, list[str]] = {}
    for name, directory in store.directories.items():
        by_store_release.setdefault(directory.release, []).append(name)
    for node in sorted(installed - matched):
        release = graph.nodes[node].release
        directories = sorted(by_store_release.get(release, ()))
        if any(node in direct[name] for name in directories):
            failing = {
                d
                for name in directories
                if node in direct[name]
                for d in unfollowed(store.directories[name], node)
            }
            through.append(f"node {node} ({', '.join(sorted(failing))})")
            continue
        mismatches.append(
            f"Node {node} matches no store directory of {release}"
            + ("" if directories else f": Bun installs no {release} here")
        )
        columns = {
            f"graph {node}": {d: node_release(t) for d, t in edges[node].items()}
        }
        for name in directories:
            columns[f"Bun {name}"] = {
                d: store_release(t) for d, t in store.directories[name].links.items()
            }
        mismatches.extend(_table(columns))

    for workspace in sorted(store.workspaces.keys() ^ graph.workspaces.keys()):
        mismatches.append(
            f"Workspace {workspace} is declared in package.json but missing from the graph"
            if workspace in store.workspaces
            else f"Workspace {workspace} is in the graph but not declared in package.json"
        )
    # Each workspace's links, and the store's hoisted fallback directory,
    # which Bun links like a workspace's node_modules.
    roots_by_owner = [
        (
            f"Workspace {workspace}",
            f"workspace {workspace}'s",
            roots,
            store.workspaces[workspace],
        )
        for workspace, roots in sorted(graph.workspaces.items())
        if workspace in store.workspaces
    ] + [("The store's fallback", "the fallback's", graph.hoisted, store.hoisted)]
    for owner, possessive, roots, links in roots_by_owner:
        expected = {
            dependency: target
            for dependency, target in roots.items()
            if target in installed or target.startswith(WORKSPACE)
        }
        for dependency in sorted(expected.keys() | links.keys()):
            edge = expected.get(dependency)
            link = links.get(dependency)
            if edge is not None and link is not None:
                if follows(link, edge):
                    continue
                if node_release(edge) == store_release(link):
                    through.append(f"{possessive} {dependency}")
                    continue
            mismatches.append(
                f"{owner} links {dependency} to "
                f"{link or 'nothing'} under Bun, and to {edge or 'nothing'} "
                "in the graph"
            )
    # Every name Bun links must be the hub's, to the same package. Bun links
    # some bins to a binary their package's optional dependency ships, as
    # esbuild's own postinstall would, where the hub links the package's bin
    # script that finds the same binary; so a name agrees when Bun's file is
    # in the hub's package or one of its dependencies. The hub may link names
    # Bun leaves out: Bun links no bin of a workspace's own copy of a
    # dependency, the one the lock keys under the workspace's name, which the
    # hub links as npm does.
    owners = expected_bins(graph, installed)
    for workspace in sorted(owners.keys() & store.bins.keys()):
        for name, linked in sorted(store.bins[workspace].items()):
            bin_dependency = owners[workspace].get(name)
            bin_node = graph.workspaces[workspace].get(bin_dependency or "")
            releases = (
                {graph.nodes[bin_node].release}
                | {
                    graph.nodes[edge].release
                    for edge in graph.nodes[bin_node].edges.values()
                    if edge in installed
                }
                if bin_node is not None and bin_node in installed
                else set()
            )
            if store_release(linked) not in releases:
                mismatches.append(
                    f"Workspace {workspace} links .bin/{name} to "
                    f"{store_release(linked)} under Bun, and to "
                    f"{bin_dependency or 'nothing'} in the npm hub"
                )
    if through:
        mismatches.append(
            "Matching only by release, through a mismatch above: " + "; ".join(through)
        )
    return mismatches


class _Registry(http.server.ThreadingHTTPServer):
    """A loopback registry serving only the planned tarballs."""

    def __init__(self, tarballs: Mapping[str, Path]) -> None:
        super().__init__(("127.0.0.1", 0), _RegistryHandler)
        self.tarballs = tarballs
        self.unplanned: list[str] = []


class _RegistryHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        registry = self.server
        if not isinstance(registry, _Registry):
            raise TypeError("The registry handler serves only _Registry")
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)[1:]
        tarball = registry.tarballs.get(path)
        if tarball is None:
            registry.unplanned.append(f"GET {self.path}")
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(tarball.stat().st_size))
        self.end_headers()
        with tarball.open("rb") as source:
            shutil.copyfileobj(source, self.wfile)

    @override
    def log_message(self, format: str, *args: object) -> None:
        # A line per tarball would bury the install's own output.
        pass


def install(
    bun: Path, root: Path, destination: Path, tarballs: Mapping[str, Path]
) -> None:
    """A frozen isolated install of root's lock and workspace manifests, under
    its bunfig.toml, whose hoistPattern decides what the fallback holds.

    tarballs maps each registry path Bun may request to its file, which Bazel
    downloaded into the host's download store and verified against the locked
    integrity, so the install adds no network fetch.
    """
    for path in [
        "bun.lock",
        "bunfig.toml",
        *(f"{w}/package.json" for w in declared_workspaces(root)),
    ]:
        (destination / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / path, destination / path)
    scratch = destination.parent / f"{destination.name}-scratch"
    for name in ("home", "tmp", "cache"):
        (scratch / name).mkdir(parents=True)
    registry = _Registry(tarballs)
    server = threading.Thread(target=registry.serve_forever, daemon=True)
    server.start()
    try:
        result = subprocess.run(
            [
                str(bun),
                "install",
                "--frozen-lockfile",
                # The graph has no lifecycle scripts to model: none runs in a
                # unit (docs/npm.md sec."Install steps").
                "--ignore-scripts",
                "--linker=isolated",
                "--no-progress",
            ],
            cwd=destination,
            # No PATH and no proxy variables: the loopback registry is the
            # install's only route to a package.
            env={
                "PATH": "",
                "HOME": str(scratch / "home"),
                "TMPDIR": str(scratch / "tmp"),
                "BUN_INSTALL_CACHE_DIR": str(scratch / "cache"),
                "BUN_CONFIG_REGISTRY": f"http://127.0.0.1:{registry.server_address[1]}/",
                "CI": "1",
                "NO_COLOR": "1",
            },
            check=False,
        )
    finally:
        registry.shutdown()
        registry.server_close()
    # Checked first: an install that also failed usually failed because of it.
    if registry.unplanned:
        raise RuntimeError(
            f"bun install requested what Bazel did not download: {registry.unplanned}"
        )
    if result.returncode != 0:
        raise RuntimeError(f"bun install failed with exit {result.returncode}")


def print_comparison(store: Store, graph: Graph) -> bool:
    """Print the comparison; True when Bun's install and the graph differ."""
    os_name, cpu = host_platform()
    mismatches = compare(store, graph, os_name, cpu)
    for line in mismatches:
        print(line)
    if mismatches:
        print(
            f"::error::the npm graph differs from Bun's install on "
            f"{os_name}/{cpu} (listed above): the translator's model of Bun's "
            "linking no longer holds (npm_graph.ts)",
            flush=True,
        )
        return True
    print(
        f"npm graph matches Bun's install on {os_name}/{cpu}: "
        f"{len(store.directories)} store directories",
        flush=True,
    )
    return False


def stage(inputs: Mapping[str, str], root: Path) -> None:
    """Lay out the lock, bunfig.toml and workspace manifests under root, each
    at its path from the lock's directory, from the runfiles path naming it."""
    for path, source in inputs.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, root / path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument(
        "--inputs",
        type=Path,
        required=True,
        help="JSON of `files`, each path from the lock's directory to the file"
        " there, and `tarballs`, each repository to its downloaded tarball",
    )
    parser.add_argument("--bun", type=Path, required=True)
    args = parser.parse_args()
    graph = read_graph(json.loads(args.graph.read_text()))
    inputs = _object(json.loads(args.inputs.read_text()), "inputs")
    files = _string_map(inputs.get("files"), "inputs.files")
    tarballs = _string_map(inputs.get("tarballs"), "inputs.tarballs")
    unknown = sorted(set(tarballs) - set(graph.tarballs))
    if unknown:
        raise ValueError(f"tarballs of repositories the graph lacks: {unknown}")
    scratch = Path(os.environ.get("TEST_TMPDIR") or os.environ["TMPDIR"])
    root = scratch / "lock"
    installed = scratch / "installed"
    stage(files, root)
    installed.mkdir()
    install(
        # Absolute, not resolved: //npm:bun may be a launcher that finds its
        # Bun from the runfiles path it was invoked by, and resolving the
        # symlink Bazel stages there moves it out of the tree.
        args.bun.absolute(),
        root,
        installed,
        {graph.tarballs[name]: Path(path).resolve() for name, path in tarballs.items()},
    )
    return 1 if print_comparison(read_store(installed, graph), graph) else 0


if __name__ == "__main__":
    sys.exit(main())
