import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  chmodSync,
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";

const source = import.meta.dirname;
const closure = JSON.parse(readFileSync(join(source, "closure.json"), "utf8"));
assert.equal(closure.format, 1, "Unsupported closure format");
const restored = mkdtempSync(join(tmpdir(), "bun-restored-"));
try {
  for (const entry of closure.entries) {
    const destination = join(restored, entry.path);
    mkdirSync(dirname(destination), { recursive: true });
    if (entry.directory) {
      mkdirSync(destination, { recursive: true });
    } else if (entry.symlink) {
      symlinkSync(entry.symlink, destination);
    } else {
      const input = join(source, "workspace", entry.path);
      assert.equal(
        createHash("sha256").update(readFileSync(input)).digest("hex"),
        entry.sha256,
        `Payload digest mismatch: ${entry.path}`,
      );
      copyFileSync(input, destination);
      chmodSync(destination, entry.executable ? 0o555 : 0o444);
    }
  }
  const lifecycle = JSON.parse(
    readFileSync(join(restored, "lifecycle.json"), "utf8"),
  );
  // A Node-invoking lifecycle must use the consumer's declared runtime.
  assert.equal(lifecycle.node, process.versions.node);
  assert.match(lifecycle.output, /answer = 42/);
  const script = `
    const assert = require('node:assert/strict');
    const { createRequire } = require('node:module');
    const local = createRequire(process.cwd() + '/package.json');
    const result = local('esbuild').transformSync('const value: number = 123;', { loader: 'ts' });
    assert.match(result.code, /value = 123/);
  `;
  for (const [name, args] of [
    ["node", ["--eval", script]],
    ["bun", ["--no-install", "--eval", script]],
  ]) {
    const result = spawnSync(join(source, name), args, {
      cwd: restored,
      env: { PATH: source, HOME: restored, TMPDIR: restored },
      encoding: "utf8",
    });
    if (result.error) throw result.error;
    assert.equal(result.status, 0, result.stdout + result.stderr);
  }
  console.log(
    "Independent consumer: lifecycle output and native esbuild passed under Node and Bun.",
  );
} finally {
  rmSync(restored, { recursive: true, force: true });
}
