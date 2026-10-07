// Bun's isolated store laid out by hand, as an install that lost the race
// leaves it: whether Bun links a bin inside a dependency cycle depends on
// install timing, so a real install cannot be made to take the losing branch.
import assert from "node:assert/strict";
import {
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readlinkSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { completeCyclicBins } from "../bun/layout.mjs";

function store(t) {
  const workspace = mkdtempSync(join(tmpdir(), "layout-"));
  t.after(() => rmSync(workspace, { recursive: true, force: true }));
  const modules = (entry) =>
    join(workspace, "node_modules", ".bun", entry, "node_modules");
  return {
    workspace,
    modules,
    package(entry, name, version, files = {}) {
      const directory = join(modules(entry), name);
      mkdirSync(directory, { recursive: true });
      writeFileSync(
        join(directory, "package.json"),
        JSON.stringify({ name, version }),
      );
      for (const [path, contents] of Object.entries(files)) {
        writeFileSync(join(directory, path), contents, { mode: 0o644 });
      }
    },
    depend(entry, alias, target, name) {
      symlinkSync(
        `../../${target}/node_modules/${name}`,
        join(modules(entry), alias),
      );
    },
  };
}

test("a cycle's skipped bin is linked as Bun links it", (t) => {
  const layout = store(t);
  layout.package("tool@1.0.0", "tool", "1.0.0", {
    "cli.js": "#!/usr/bin/env node\r\nrun();\r\n",
  });
  layout.package("plugin@1.0.0", "plugin", "1.0.0");
  layout.depend("tool@1.0.0", "plugin", "plugin@1.0.0", "plugin");
  layout.depend("plugin@1.0.0", "tool", "tool@1.0.0", "tool");
  const added = completeCyclicBins(layout.workspace, {
    "tool@1.0.0": { bin: { tool: "cli.js" } },
  });
  assert.equal(added, 1);
  const link = join(layout.modules("plugin@1.0.0"), ".bin", "tool");
  assert.equal(readlinkSync(link), "../tool/cli.js");
  const file = join(layout.modules("tool@1.0.0"), "tool", "cli.js");
  // Bun's linker leaves the bin executable and its shebang without a carriage return.
  assert.equal(lstatSync(file).mode & 0o111, 0o111);
  assert.equal(readFileSync(file, "utf8"), "#!/usr/bin/env node\nrun();\r\n");
});

test("a dependency outside a cycle keeps the links Bun gave it", (t) => {
  // Bun waits for such a dependency before linking, so a missing link there
  // is Bun's decision, not timing, even beside a dependency in a cycle.
  const layout = store(t);
  layout.package("tool@1.0.0", "tool", "1.0.0", {
    "cli.js": "#!/usr/bin/env node\n",
  });
  layout.package("peer@1.0.0", "peer", "1.0.0");
  layout.package("user@1.0.0", "user", "1.0.0");
  layout.depend("user@1.0.0", "tool", "tool@1.0.0", "tool");
  layout.depend("user@1.0.0", "peer", "peer@1.0.0", "peer");
  layout.depend("peer@1.0.0", "user", "user@1.0.0", "user");
  assert.equal(
    completeCyclicBins(layout.workspace, {
      "tool@1.0.0": { bin: { tool: "cli.js" } },
    }),
    0,
  );
  assert.throws(() => lstatSync(join(layout.modules("user@1.0.0"), ".bin")), {
    code: "ENOENT",
  });
});

test("the first dependency by name keeps a bin name two of them declare", (t) => {
  // Bun links dependencies in name order and never replaces a claimed bin.
  const layout = store(t);
  for (const name of ["alpha", "beta"]) {
    layout.package(`${name}@1.0.0`, name, "1.0.0", {
      "cli.js": "#!/usr/bin/env node\n",
    });
    layout.depend(`${name}@1.0.0`, "host", "host@1.0.0", "host");
  }
  layout.package("host@1.0.0", "host", "1.0.0");
  layout.depend("host@1.0.0", "beta", "beta@1.0.0", "beta");
  layout.depend("host@1.0.0", "alpha", "alpha@1.0.0", "alpha");
  const bin = { bin: { shared: "cli.js" } };
  completeCyclicBins(layout.workspace, {
    "alpha@1.0.0": bin,
    "beta@1.0.0": bin,
  });
  assert.equal(
    readlinkSync(join(layout.modules("host@1.0.0"), ".bin", "shared")),
    "../alpha/cli.js",
  );
});
