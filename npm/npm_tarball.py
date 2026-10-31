"""Check an npm package tarball's install steps, or unpack it as its per-file unit.

Each subcommand reads the tarball as npm does, by each member's path with its
first component stripped, whatever that component is named (docs/npm.md
sec."Units"). The reading is rules_archive_store's archive_to_tar.py's, so a
tarball is judged under the same member checks and bounds as the archive
conversion of it, before anything is read whole or written.

`check` fails on a `preinstall`, `install` or `postinstall` script in the
tarball's own package.json, or a `binding.gyp` at the package's root, unless an
`--exempt` names that step with its digest. A script's digest is of its command
as package.json holds it, so a release that keeps the command keeps its
exemption, whatever the file the command runs. The registry's manifest is never
read, since it can name a script the tarball lacks. With `--passed`, a check
that passes also copies the tarball there, which is what the archive unit is
converted from, so reading the unit in any configuration runs the check.

`exemptions` fails when an exemption matches no step that `check` found in any
locked release of its package, so no entry outlives what it was written for.

`unpack` writes the package's tree into a directory. The tree holds only
directories and regular files, because a link inside a directory output reaches
a consumer as a copy, so a link member is refused rather than dropped. A file
keeps only whether it is executable, which is all remote execution carries of
its mode.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import tarfile
from collections.abc import Sequence
from pathlib import Path

# rules_archive_store's libraries, which Bazel puts on the path from that
# module's repository. A type check outside Bazel does not find them, and a
# single-file module cannot carry py.typed; that module checks them under
# mypy --strict itself.
from archive_parts import (  # type: ignore[import-untyped, import-not-found, unused-ignore]
    Parts,
    PartsError,
    parts_of,
)
from archive_to_tar import (  # type: ignore[import-untyped, import-not-found, unused-ignore]
    ArchiveError,
    Member,
    checked_tar_members,
    tar_contents,
)

INSTALL_SCRIPTS = ("preinstall", "install", "postinstall")
BINDING = "binding.gyp"
_MANIFEST = "package.json"
# package.json is the one member read whole. The largest locked one is under
# 100 KiB, so this bounds memory with room for any real manifest.
MANIFEST_MAX = 1 << 20
_EXEMPTIONS = "the npm.graph tag's exemptions file"


class TarballError(ValueError):
    """The tarball is not one the npm rules install."""


def _members(tarball: Path) -> tuple[Parts, list[Member | None]]:  # type: ignore[no-any-unimported, unused-ignore]
    parts = parts_of([tarball])
    try:
        return parts, checked_tar_members(parts, strip_components=1)
    except ArchiveError as error:
        raise TarballError(str(error)) from error


def install_steps(tarball: Path) -> dict[str, str]:
    """Each install script and binding.gyp the tarball holds, by name, to the
    SHA-256 an exemption names it by."""
    parts, members = _members(tarball)
    manifest: bytes | None = None
    steps: dict[str, str] = {}
    for member, content in zip(members, tar_contents(parts), strict=True):
        if member is None:
            continue
        # The archive conversion keeps an in-tree link, and an installer may
        # read binding.gyp through one, so only a regular file is judged.
        if member.name in (BINDING, _MANIFEST) and member.kind != stat.S_IFREG:
            raise TarballError(f"{tarball}'s root {member.name} is not a regular file")
        if member.kind != stat.S_IFREG or content is None:
            continue
        if member.name == BINDING:
            steps[BINDING] = member.content.hex()
        elif member.name == _MANIFEST:
            if member.size > MANIFEST_MAX:
                raise TarballError(
                    f"{tarball}'s package.json is {member.size} bytes, over"
                    f" the {MANIFEST_MAX}-byte bound"
                )
            with content() as stream:
                manifest = stream.read()
    if manifest is None:
        raise TarballError(f"{tarball} holds no package.json at its root")
    scripts = json.loads(manifest).get("scripts") or {}
    for script in INSTALL_SCRIPTS:
        command = scripts.get(script)
        if isinstance(command, str):
            steps[script] = hashlib.sha256(command.encode("utf-8")).hexdigest()
    return steps


def check(tarball: Path, package: str, exemptions: dict[str, str]) -> str:
    """The exempted steps, one `<step> <digest>` per line, or TarballError
    naming each step no exemption matches."""
    steps = install_steps(tarball)
    unexempted = [
        f"{package}: {step} (sha256 {digest})"
        for step, digest in sorted(steps.items())
        if exemptions.get(step) != digest
    ]
    if unexempted:
        raise TarballError(
            f"an install step no exemption in {_EXEMPTIONS} matches, which no"
            " unit runs; review it and exempt it with its digest, or give it an"
            " action of the consumer's own:\n  " + "\n  ".join(unexempted)
        )
    return "".join(f"{step} {digest}\n" for step, digest in sorted(steps.items()))


def stale_exemptions(
    package: str, exemptions: dict[str, str], checked: list[str]
) -> list[str]:
    """The exemptions of `package` matching no step that any of `checked`,
    the stamps of its locked releases' checks, records."""
    found = {tuple(line.split()) for text in checked for line in text.splitlines()}
    return [
        f"{package}: {step} (sha256 {digest})"
        for step, digest in sorted(exemptions.items())
        if (step, digest) not in found
    ]


def unpack(tarball: Path, into: Path, executables: Sequence[str] = ()) -> None:
    """Write the package's tree into `into`, or raise TarballError.

    Each of `executables`, a file the package's `bin` names, is marked
    executable whatever mode the tarball gave it, so a tool started by path
    runs.
    """
    parts, members = _members(tarball)
    links = [
        member.name
        for member in members
        if member is not None and member.kind not in (stat.S_IFDIR, stat.S_IFREG)
    ]
    if links:
        raise TarballError(
            f"{tarball} holds links, which a unit cannot hold: {', '.join(links)}"
        )
    into.mkdir(parents=True, exist_ok=True)
    for member, content in zip(members, tar_contents(parts), strict=True):
        if member is None:
            continue
        target = into / member.name
        if member.kind == stat.S_IFDIR:
            target.mkdir(mode=0o755, parents=True, exist_ok=True)
            continue
        assert content is not None
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        with content() as stream, target.open("xb") as written:
            shutil.copyfileobj(stream, written)
        target.chmod(0o755 if member.mode & 0o111 else 0o644)
    root = into.resolve()
    for path in executables:
        components = path.split("/")
        if path.startswith("/") or any(c in ("", ".", "..") for c in components):
            raise TarballError(f"{tarball}: bin {path!r} is not a path in the package")
        target = into / path
        if (
            target.is_symlink()
            or not target.is_file()
            or not target.resolve().is_relative_to(root)
        ):
            raise TarballError(f"{tarball}: bin {path} is not a file in the package")
        target.chmod(0o755)


def _exempt(entries: list[str]) -> dict[str, str]:
    return dict(entry.split("=", 1) for entry in entries)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    exempting = argparse.ArgumentParser(add_help=False)
    exempting.add_argument("--package", required=True)
    exempting.add_argument(
        "--exempt",
        action="append",
        default=[],
        metavar="STEP=SHA256",
        help="an install step the package may hold, by its digest",
    )
    exempting.add_argument("--stamp", type=Path, required=True)
    checking = commands.add_parser("check", parents=[exempting])
    checking.add_argument("--tarball", type=Path, required=True)
    checking.add_argument(
        "--passed",
        type=Path,
        help="where a check that passes copies the tarball",
    )
    auditing = commands.add_parser("exemptions", parents=[exempting])
    auditing.add_argument(
        "--checked",
        type=Path,
        action="append",
        default=[],
        help="the stamp `check` wrote for a locked release of the package",
    )
    unpacking = commands.add_parser("unpack")
    unpacking.add_argument("--tarball", type=Path, required=True)
    unpacking.add_argument("--into", type=Path, required=True)
    unpacking.add_argument(
        "--executable",
        action="append",
        default=[],
        help="a file the package's bin names, by its path in the package",
    )
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "check":
            exemptions = _exempt(arguments.exempt)
            arguments.stamp.write_text(
                check(arguments.tarball, arguments.package, exemptions),
                encoding="ascii",
            )
            if arguments.passed is not None:
                shutil.copyfile(arguments.tarball, arguments.passed)
        elif arguments.command == "exemptions":
            checked = [path.read_text(encoding="ascii") for path in arguments.checked]
            stale = stale_exemptions(
                arguments.package, _exempt(arguments.exempt), checked
            )
            if stale:
                raise TarballError(
                    f"an exemption in {_EXEMPTIONS} matches no step in any"
                    " locked release of its package; remove it:\n  "
                    + "\n  ".join(stale)
                )
            arguments.stamp.write_text("", encoding="ascii")
        else:
            unpack(arguments.tarball, arguments.into, arguments.executable)
    except (
        TarballError,
        ArchiveError,
        PartsError,
        OSError,
        tarfile.TarError,
        json.JSONDecodeError,
    ) as error:
        parser.exit(1, f"npm_tarball {arguments.command}: error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
