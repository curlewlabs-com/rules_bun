"""Finish a development toolchain bump: Bun's archive digests and the README.

A bump moves the version literals: Bun's in the release URL of each file in
BUN_PINS, Node's in each file in NODE_PINS, Bazel's in each file in
BAZEL_PINS. Two things follow from those literals and cannot be typed from
them: the sha256 of each host's Bun archive, which the release publishes in
SHASUMS256.txt, and the README, which names the versions and links the
licenses of the releases in use. This writes both.

    python3 tools/refresh_toolchain.py

It fails, changing nothing, when the files pinning one tool disagree about
its version, when a pinned archive is missing from the release's digest list,
or when the README no longer holds a sentence or link it rewrites.
tests/runtime_updates.bzl is deliberately outside it: those older runtimes
exist to differ from the ones pinned here.
"""

from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BUN_PINS = (
    "tests/deps.bzl",
    "tests/npm_consumer/deps.bzl",
    "examples/consumer/deps.bzl",
)
NODE_PINS = ("MODULE.bazel", "examples/consumer/MODULE.bazel")
BAZEL_PINS = (
    ".bazelversion",
    "tests/npm_consumer/.bazelversion",
    "examples/consumer/.bazelversion",
)
README = "README.md"

_VERSION = r"[0-9]+\.[0-9]+\.[0-9]+"
_BUN_RELEASE = re.compile(rf"/oven-sh/bun/releases/download/bun-v({_VERSION})/")
_NODE_VERSION = re.compile(rf'node_version = "({_VERSION})"')
_BAZEL_VERSION = re.compile(rf"^({_VERSION})$", re.MULTILINE)
# A host's archive name beside its digest, as each _BUN table writes them.
_ARCHIVE_DIGEST = re.compile(r'\("(?P<archive>[a-z0-9-]+)", "(?P<sha256>[0-9a-f]{64})"')


class RefreshError(Exception):
    """The pins or the README are not in a state this can finish a bump from."""


def pinned_version(sources: dict[str, str], pattern: re.Pattern[str], tool: str) -> str:
    """The one version of `tool` that every file in `sources` pins."""
    found: dict[str, list[str]] = {
        path: sorted(set(pattern.findall(text))) for path, text in sources.items()
    }
    versions = {version for versions in found.values() for version in versions}
    if len(versions) != 1 or not all(found.values()):
        raise RefreshError(f"{tool} is not pinned to one version: {found}")
    return versions.pop()


def parse_shasums(listing: str) -> dict[str, str]:
    """Each file's sha256 by name, from a release's SHASUMS256.txt."""
    digests: dict[str, str] = {}
    for line in listing.splitlines():
        if line.strip():
            sha256, name = line.split()
            digests[name.removeprefix("*")] = sha256
    return digests


def rewrite_digests(text: str, digests: dict[str, str]) -> str:
    """`text` with each host archive's digest replaced by the release's."""

    def published(match: re.Match[str]) -> str:
        name = f"bun-{match['archive']}.zip"
        if name not in digests:
            raise RefreshError(f"the release publishes no digest for {name}")
        return f'("{match["archive"]}", "{digests[name]}"'

    rewritten, count = _ARCHIVE_DIGEST.subn(published, text)
    if not count:
        raise RefreshError("no host archive digest found to rewrite")
    return rewritten


def rewrite_readme(text: str, bazel: str, bun: str, node: str) -> str:
    """`text` naming these versions and linking these releases' licenses."""
    replacements = (
        (rf"\*\*Bazel {_VERSION}\*\*", f"**Bazel {bazel}**"),
        (rf"\*\*Bun {_VERSION}\*\*", f"**Bun {bun}**"),
        (rf"\*\*Node {_VERSION}\*\*", f"**Node {node}**"),
        (
            rf"/oven-sh/bun/blob/bun-v{_VERSION}/LICENSE\.md",
            f"/oven-sh/bun/blob/bun-v{bun}/LICENSE.md",
        ),
        (
            rf"/nodejs/node/blob/v{_VERSION}/LICENSE",
            f"/nodejs/node/blob/v{node}/LICENSE",
        ),
    )
    for pattern, replacement in replacements:
        text, count = re.subn(pattern, replacement, text)
        if count != 1:
            raise RefreshError(
                f"README matches {pattern!r} {count} times; expected once"
            )
    return text


def fetch_shasums(bun: str) -> str:
    url = f"https://github.com/oven-sh/bun/releases/download/bun-v{bun}/SHASUMS256.txt"
    with urllib.request.urlopen(url, timeout=60) as response:
        listing: bytes = response.read()
    return listing.decode()


def refresh(root: Path, shasums: str | None = None) -> list[str]:
    """Rewrite the digests and the README under `root`; return what changed.

    `shasums` is the release's digest list when the caller already holds it.
    """

    def read(paths: tuple[str, ...]) -> dict[str, str]:
        return {path: (root / path).read_text() for path in paths}

    bun_sources = read(BUN_PINS)
    bun = pinned_version(bun_sources, _BUN_RELEASE, "Bun")
    node = pinned_version(read(NODE_PINS), _NODE_VERSION, "Node")
    bazel = pinned_version(read(BAZEL_PINS), _BAZEL_VERSION, "Bazel")

    digests = parse_shasums(fetch_shasums(bun) if shasums is None else shasums)
    # Every rewrite is computed before any is written, so a failure leaves
    # the tree as it was.
    rewritten = {
        path: rewrite_digests(text, digests) for path, text in bun_sources.items()
    }
    before = dict(bun_sources)
    before[README] = (root / README).read_text()
    rewritten[README] = rewrite_readme(before[README], bazel, bun, node)

    changed = [path for path, text in rewritten.items() if text != before[path]]
    for path in changed:
        (root / path).write_text(rewritten[path])
    return changed


def main() -> int:
    try:
        changed = refresh(ROOT)
    except RefreshError as error:
        print(f"refresh_toolchain: {error}", file=sys.stderr)
        return 1
    print("refresh_toolchain: " + (", ".join(changed) or "nothing to change"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
