import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  chmodSync,
  symlinkSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";

const source = resolve(process.argv[2]);
// The declared Bun, as a consumer supplies its own; the repository's runtime
// links are private to acquisition.
const bun = resolve(process.argv[3]);
const workspaceOnly = process.argv[4] === "--workspace-only";
const closure = JSON.parse(readFileSync(join(source, "closure.json"), "utf8"));
const restored = mkdtempSync(join(tmpdir(), "bun-consumer-"));
try {
  // Restoring only exported payload proves package links do not reach the producer.
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
        entry.path,
      );
      copyFileSync(input, destination);
      chmodSync(destination, entry.executable ? 0o555 : 0o444);
    }
  }
  const script = `
    const assert = require('node:assert/strict');
    const { createRequire } = require('node:module');
    const local = createRequire(process.cwd() + '/package.json');
    const workspace = createRequire(process.cwd() + '/examples/workspace/package.json');
    assert.equal(workspace('is-number')(42), true);
    if (${workspaceOnly}) {
      assert.throws(() => local.resolve('esbuild'), { code: 'MODULE_NOT_FOUND' });
    } else {
      const result = local('esbuild').transformSync('const answer: number = 42;', { loader: 'ts' });
      assert.match(result.code, /answer = 42/);
    }
  `;
  for (const [executable, args] of [
    [process.execPath, ["--eval", script]],
    [bun, ["--no-install", "--eval", script]],
  ]) {
    const result = spawnSync(executable, args, {
      cwd: restored,
      env: {
        PATH: dirname(process.execPath),
        HOME: restored,
        TMPDIR: restored,
      },
      encoding: "utf8",
    });
    if (result.error) throw result.error;
    assert.equal(result.status, 0, result.stdout + result.stderr);
  }
  if (!workspaceOnly) {
    // Losing a native dependency must fail instead of falling back to a host tool.
    const native = closure.entries.filter(
      (entry) =>
        entry.sha256 &&
        entry.executable &&
        entry.path.includes("/node_modules/@esbuild/"),
    );
    assert.equal(native.length, 1);
    rmSync(join(restored, native[0].path));
    const failed = spawnSync(process.execPath, ["--eval", script], {
      cwd: restored,
      env: {
        PATH: dirname(process.execPath),
        HOME: restored,
        TMPDIR: restored,
      },
      encoding: "utf8",
    });
    if (failed.error) throw failed.error;
    assert.notEqual(
      failed.status,
      0,
      "Missing native executable unexpectedly worked",
    );
    assert.match(failed.stderr, /esbuild|ENOENT/);
  }
  console.log(
    `Relocated consumer passed under Node and Bun (workspaceOnly=${workspaceOnly}).`,
  );
} finally {
  rmSync(restored, { recursive: true, force: true });
}
