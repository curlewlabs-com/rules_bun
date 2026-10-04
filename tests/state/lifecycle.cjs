const assert = require("node:assert/strict");
const { readFileSync, writeFileSync } = require("node:fs");
const { join } = require("node:path");
const { execFileSync } = require("node:child_process");
const { transformSync } = require("esbuild");

async function main() {
  const { identity, barrier } = JSON.parse(readFileSync("state.json", "utf8"));
  const scratch = ["HOME", "TMPDIR", "BUN_INSTALL_CACHE_DIR"].map(
    (key) => process.env[key],
  );
  // Exclusive writes expose reused state even when a previous install succeeded.
  for (const directory of scratch) {
    writeFileSync(join(directory, "acquisition-owner"), identity, { flag: "wx" });
  }
  if (barrier) {
    // Both installs must hold private state before either is allowed to finish.
    const response = await fetch(`${barrier}/${identity}`);
    assert.equal(response.status, 200);
  }
  for (const directory of scratch) {
    assert.equal(readFileSync(join(directory, "acquisition-owner"), "utf8"), identity);
  }
  writeFileSync(
    "lifecycle.json",
    JSON.stringify({
      identity,
      scratch,
      node: process.versions.node,
      bun: execFileSync("bun", ["--version"], { encoding: "utf8" }).trim(),
      output: transformSync("const answer: number = 42;", { loader: "ts" }).code,
    }),
  );
}

main().catch((error) => {
  console.error("Acquisition state lifecycle failed:", error);
  process.exitCode = 1;
});
