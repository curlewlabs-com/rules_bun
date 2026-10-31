"""npm_tarball's install-step check and per-file unpack, over tarballs it writes.

The check is what keeps any install step from being silently skipped, so each
case is a tarball the check must refuse or must let through.
"""

from __future__ import annotations

import hashlib
import io
import json
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import override
from unittest import mock

# Untyped here for the reason npm_tarball.py gives at its import.
import archive_to_tar  # type: ignore[import-untyped, import-not-found, unused-ignore]
import npm_tarball

# A member: name, tarfile type, mode, content, link name.
type Member = tuple[str, bytes, int, bytes, str]


def _sha256(text: str | bytes) -> str:
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


def _manifest(scripts: dict[str, str] | None = None) -> bytes:
    return json.dumps(
        {"name": "tool", "version": "1.0.0", "scripts": scripts or {}}
    ).encode()


def _file(name: str, content: bytes, mode: int = 0o644) -> Member:
    return (name, tarfile.REGTYPE, mode, content, "")


class NpmTarballTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def tarball(self, members: list[Member], name: str = "package") -> Path:
        path = self.root / f"{name}.tgz"
        with tarfile.open(path, "w:gz") as archive:
            for member_name, kind, mode, content, link in members:
                info = tarfile.TarInfo(member_name)
                info.type, info.mode, info.linkname = kind, mode, link
                info.size = len(content) if kind == tarfile.REGTYPE else 0
                archive.addfile(info, io.BytesIO(content) if content else None)
        return path

    def test_an_unexempted_install_script_fails_naming_package_step_and_digest(
        self,
    ) -> None:
        tarball = self.tarball(
            [
                _file(
                    "package/package.json",
                    _manifest({"postinstall": "node install.js"}),
                )
            ]
        )
        with self.assertRaises(npm_tarball.TarballError) as raised:
            npm_tarball.check(tarball, "tool", {})
        message = str(raised.exception)
        self.assertIn("tool: postinstall", message)
        self.assertIn(_sha256("node install.js"), message)

    def test_an_exemption_holds_only_while_the_command_is_unchanged(self) -> None:
        # A bump that keeps the command keeps its entry; one that changes it
        # must fail until the entry is reviewed again.
        exemptions = {"postinstall": _sha256("node install.js")}
        kept = self.tarball(
            [
                _file(
                    "package/package.json",
                    _manifest({"postinstall": "node install.js"}),
                )
            ],
            "kept",
        )
        self.assertEqual(
            npm_tarball.check(kept, "tool", exemptions),
            f"postinstall {_sha256('node install.js')}\n",
        )
        changed = self.tarball(
            [
                _file(
                    "package/package.json",
                    _manifest({"postinstall": "node install.js --force"}),
                )
            ],
            "changed",
        )
        with self.assertRaises(npm_tarball.TarballError):
            npm_tarball.check(changed, "tool", exemptions)
        # An exemption covers only the step it names.
        other = self.tarball(
            [
                _file(
                    "package/package.json", _manifest({"preinstall": "node install.js"})
                )
            ],
            "other",
        )
        with self.assertRaises(npm_tarball.TarballError):
            npm_tarball.check(other, "tool", exemptions)

    def test_a_binding_gyp_fails_without_any_script(self) -> None:
        # npm runs `node-gyp rebuild` for a binding.gyp the package names no
        # install script for, so the file alone is an install step.
        tarball = self.tarball(
            [
                _file("package/package.json", _manifest()),
                _file("package/binding.gyp", b"{}"),
            ]
        )
        with self.assertRaises(npm_tarball.TarballError) as raised:
            npm_tarball.check(tarball, "tool", {})
        self.assertIn("tool: binding.gyp", str(raised.exception))
        self.assertEqual(
            npm_tarball.check(tarball, "tool", {"binding.gyp": _sha256(b"{}")}),
            f"binding.gyp {_sha256(b'{}')}\n",
        )

    def test_a_root_install_file_that_is_a_link_fails(self) -> None:
        # A link would hide the step from a check that reads only regular
        # files, while the archive conversion keeps it and an installer may
        # follow it.
        cases: dict[str, list[Member]] = {
            "symlinked binding.gyp": [
                _file("package/package.json", _manifest()),
                _file("package/build.gyp", b"{}"),
                ("package/binding.gyp", tarfile.SYMTYPE, 0o777, b"", "build.gyp"),
            ],
            "hard-linked binding.gyp": [
                _file("package/package.json", _manifest()),
                _file("package/build.gyp", b"{}"),
                (
                    "package/binding.gyp",
                    tarfile.LNKTYPE,
                    0o644,
                    b"",
                    "package/build.gyp",
                ),
            ],
            "symlinked package.json": [
                _file("package/real.json", _manifest({"postinstall": "x"})),
                ("package/package.json", tarfile.SYMTYPE, 0o777, b"", "real.json"),
            ],
        }
        for index, (case, members) in enumerate(cases.items()):
            with self.subTest(case), self.assertRaises(npm_tarball.TarballError):
                npm_tarball.check(self.tarball(members, f"linked-{index}"), "tool", {})

    def test_the_check_reads_the_package_root_whatever_its_top_is_named(self) -> None:
        # Some tarballs' top directory is not `package` (`node v22.20` among
        # @types), and a nested package.json or binding.gyp is not the
        # package's own.
        tarball = self.tarball(
            [
                _file("node v22.20/package.json", _manifest({"install": "make"})),
                _file("node v22.20/vendor/package.json", _manifest()),
                _file("node v22.20/vendor/binding.gyp", b"{}"),
            ]
        )
        with self.assertRaises(npm_tarball.TarballError) as raised:
            npm_tarball.check(tarball, "tool", {})
        self.assertIn("tool: install", str(raised.exception))
        self.assertNotIn("binding.gyp", str(raised.exception))

    def test_a_tarball_without_one_package_json_is_refused(self) -> None:
        for index, members in enumerate(
            (
                [_file("package/index.js", b"")],
                [
                    _file("package/package.json", _manifest()),
                    _file("other/package.json", _manifest({"postinstall": "x"})),
                ],
            )
        ):
            with self.subTest(index=index), self.assertRaises(npm_tarball.TarballError):
                npm_tarball.check(
                    self.tarball(members, f"manifests-{index}"), "tool", {}
                )

    def test_unpack_roots_the_tree_and_keeps_only_the_executable_bit(self) -> None:
        tarball = self.tarball(
            [
                ("types", tarfile.DIRTYPE, 0o700, b"", ""),
                _file("types/package.json", _manifest(), 0o600),
                _file("types/bin/run", b"#!/bin/sh\n", 0o4750),
                # npm's tarballs name some files twice, once through `./`.
                _file("types/./lib/index.js", b"same"),
                _file("types/lib/index.js", b"same"),
            ]
        )
        into = self.root / "unit"
        npm_tarball.unpack(tarball, into)
        files = sorted(
            str(path.relative_to(into)) for path in into.rglob("*") if path.is_file()
        )
        self.assertEqual(files, ["bin/run", "lib/index.js", "package.json"])
        self.assertEqual(stat.S_IMODE((into / "bin/run").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((into / "package.json").stat().st_mode), 0o644)
        self.assertEqual((into / "lib/index.js").read_bytes(), b"same")

    def test_unpack_marks_each_bin_executable_and_refuses_a_missing_one(
        self,
    ) -> None:
        # A tool started by path, as a package script starts its tools, needs
        # its bin executable whatever mode the tarball gave it.
        tarball = self.tarball(
            [
                ("types", tarfile.DIRTYPE, 0o755, b"", ""),
                _file("types/package.json", _manifest()),
                _file("types/cli.js", b"cli", 0o644),
            ]
        )
        into = self.root / "unit"
        npm_tarball.unpack(tarball, into, ["cli.js"])
        self.assertEqual(stat.S_IMODE((into / "cli.js").stat().st_mode), 0o755)
        with self.assertRaisesRegex(npm_tarball.TarballError, "bin missing.js"):
            npm_tarball.unpack(tarball, self.root / "other", ["missing.js"])
        # A bin path is the package's to name, never a way out of the unit.
        for index, escape in enumerate(("../outside.js", "/etc/passwd", "./cli.js")):
            with (
                self.subTest(escape),
                self.assertRaisesRegex(npm_tarball.TarballError, "not a path"),
            ):
                npm_tarball.unpack(tarball, self.root / f"escape-{index}", [escape])

    def test_unpack_refuses_what_a_unit_cannot_hold(self) -> None:
        # A link in a directory output reaches a consumer as a copy, and a
        # repeat that differs or a path out of the package has no one tree.
        cases: dict[str, list[Member]] = {
            "symlink": [("package/alias", tarfile.SYMTYPE, 0o777, b"", "index.js")],
            "hard link": [
                _file("package/index.js", b"x"),
                ("package/again", tarfile.LNKTYPE, 0o644, b"", "package/index.js"),
            ],
            "differing repeat": [
                _file("package/./index.js", b"one"),
                _file("package/index.js", b"two"),
            ],
            "file at the top": [_file("README", b"x")],
            "leaving the package": [_file("package/../../escape", b"x")],
        }
        for index, (case, members) in enumerate(cases.items()):
            with self.subTest(case), self.assertRaises(npm_tarball.TarballError):
                npm_tarball.unpack(
                    self.tarball(members, f"refused-{index}"),
                    self.root / f"unit-{index}",
                )
        self.assertFalse((self.root / "escape").exists())

    def test_the_check_and_the_unpack_read_under_the_conversion_s_bounds(self) -> None:
        # The tarballs come from the registry, so the check and the unpack
        # read them under the archive conversion's bounds rather than their own.
        tarball = self.tarball(
            [_file(f"package/{name}", b"x") for name in ("package.json", "a", "b")]
        )
        with mock.patch.object(archive_to_tar, "MEMBERS_MAX", 2):
            with self.assertRaises(npm_tarball.TarballError):
                npm_tarball.check(tarball, "tool", {})
            with self.assertRaises(npm_tarball.TarballError):
                npm_tarball.unpack(tarball, self.root / "bounded")
        self.assertFalse((self.root / "bounded").exists())
        manifest = self.tarball([_file("package/package.json", _manifest())], "big")
        with (
            mock.patch.object(npm_tarball, "MANIFEST_MAX", 8),
            self.assertRaises(npm_tarball.TarballError),
        ):
            npm_tarball.check(manifest, "tool", {})

    def test_an_exemption_no_locked_release_matches_is_stale(self) -> None:
        # An entry whose step or command left every locked release would
        # otherwise sit in the list excusing nothing anyone re-reviewed.
        digest = _sha256("node install.js")
        checked = [f"postinstall {digest}\n", ""]
        self.assertEqual(
            npm_tarball.stale_exemptions("tool", {"postinstall": digest}, checked), []
        )
        self.assertEqual(
            npm_tarball.stale_exemptions(
                "tool", {"postinstall": digest, "install": digest}, checked
            ),
            [f"tool: install (sha256 {digest})"],
        )
        self.assertEqual(
            npm_tarball.stale_exemptions(
                "tool", {"postinstall": _sha256("x")}, checked
            ),
            [f"tool: postinstall (sha256 {_sha256('x')})"],
        )

    def test_the_cli_fails_and_writes_no_stamp_or_copy_for_an_unexempted_step(
        self,
    ) -> None:
        # The archive unit is converted from the copy, so a copy written by a
        # failing check would let a consumer read a package nobody exempted.
        tarball = self.tarball(
            [_file("package/package.json", _manifest({"postinstall": "x"}))]
        )
        stamp = self.root / "stamp"
        passed = self.root / "passed.tgz"
        arguments = [
            "check",
            "--tarball",
            str(tarball),
            "--package",
            "tool",
            "--stamp",
            str(stamp),
            "--passed",
            str(passed),
        ]
        with self.assertRaises(SystemExit) as raised:
            npm_tarball.main(arguments)
        self.assertEqual(raised.exception.code, 1)
        self.assertFalse(stamp.exists())
        self.assertFalse(passed.exists())
        self.assertEqual(
            npm_tarball.main([*arguments, "--exempt", f"postinstall={_sha256('x')}"]), 0
        )
        self.assertTrue(stamp.exists())
        self.assertEqual(passed.read_bytes(), tarball.read_bytes())


if __name__ == "__main__":
    unittest.main()
