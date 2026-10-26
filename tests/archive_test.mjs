// The closure archive must expand, with the system tar a consumer uses, to
// exactly the tree the manifest describes, and its bytes must not depend on
// when or where it was written: the archive's digest keys every action that
// declares it.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readlinkSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { writeArchive } from "../bun/archive.mjs";

// Past the ustar name and prefix fields, so it takes a PAX record.
const deep = `node_modules/.bun/${"a".repeat(120)}/node_modules/${"b".repeat(120)}/index.js`;
const longTarget = `../${"c".repeat(110)}/target.js`;

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "archive-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const workspace = join(root, "workspace");
  const write = (path, content) => {
    mkdirSync(join(workspace, path, ".."), { recursive: true });
    writeFileSync(join(workspace, path), content);
  };
  write("package.json", "{}\n");
  write("node_modules/.bin/tool", "#!/bin/sh\n");
  write(deep, "module.exports = 1;\n");
  const entries = [
    { path: "empty", directory: true },
    { path: "node_modules", directory: true },
    { path: "node_modules/.bin", directory: true },
    { path: "node_modules/.bin/tool", executable: true },
    { path: "node_modules/link", symlink: ".bin/tool" },
    { path: "node_modules/long-link", symlink: longTarget },
    { path: deep, executable: false },
    { path: "package.json", executable: false },
  ];
  return { root, workspace, entries };
}

test("the same entries give the same bytes", (t) => {
  const { root, workspace, entries } = fixture(t);
  writeArchive(workspace, entries, join(root, "first.tar"));
  writeArchive(workspace, entries, join(root, "second.tar"));
  assert.deepEqual(
    readFileSync(join(root, "first.tar")),
    readFileSync(join(root, "second.tar")),
  );
});

test("the system tar expands the archive to the described tree", (t) => {
  const { root, workspace, entries } = fixture(t);
  const archive = join(root, "closure.tar");
  writeArchive(workspace, entries, archive);
  const out = join(root, "out");
  mkdirSync(out);
  execFileSync("tar", ["-xf", archive, "-C", out]);
  assert.ok(lstatSync(join(out, "empty")).isDirectory());
  assert.equal(readlinkSync(join(out, "node_modules/link")), ".bin/tool");
  assert.equal(readlinkSync(join(out, "node_modules/long-link")), longTarget);
  assert.equal(readFileSync(join(out, deep), "utf8"), "module.exports = 1;\n");
  assert.equal(lstatSync(join(out, "node_modules/.bin/tool")).mode & 0o111, 0o111);
  assert.equal(lstatSync(join(out, "package.json")).mode & 0o111, 0);
});
