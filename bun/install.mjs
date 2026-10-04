import { createHash } from "node:crypto";
import {
  chmodSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  readlinkSync,
  realpathSync,
  writeFileSync,
} from "node:fs";
import { isAbsolute, join, relative, resolve } from "node:path";
import { spawnSync } from "node:child_process";

const repository = resolve(process.argv[2]);
const workspace = join(repository, "workspace");
const request = JSON.parse(
  readFileSync(join(repository, "request.json"), "utf8"),
);
const digest = (path) =>
  createHash("sha256").update(readFileSync(path)).digest("hex");
const manifest = JSON.parse(
  readFileSync(join(workspace, "package.json"), "utf8"),
);

// Exact directories avoid implementing a second workspace-glob resolver.
const directories = manifest.workspaces ?? [];
if (
  !Array.isArray(directories) ||
  directories.some(
    (p) =>
      typeof p !== "string" ||
      !p ||
      p.startsWith("/") ||
      p.split("/").some((s) => !s || s === "." || s === "..") ||
      /[*?{}[\]\\]/.test(p),
  )
) {
  throw new Error("workspaces must be explicit root-relative directories");
}
for (const directory of directories) {
  if (!request.inputs.includes(`${directory}/package.json`)) {
    throw new Error(
      `Missing declared workspace manifest: ${directory}/package.json`,
    );
  }
}
if (
  !request.workspaces.length ||
  request.workspaces.some((p) => p !== "." && !directories.includes(p))
) {
  throw new Error(
    "Each selected workspace must be an exact declared directory or '.'",
  );
}
const before = Object.fromEntries(
  request.inputs.map((p) => [p, digest(join(workspace, p))]),
);
for (const directory of ["home", "tmp", "cache"])
  mkdirSync(join(repository, "scratch", directory), { recursive: true });
const bun = join(repository, "tools", "bun");
const node = join(repository, "tools", "node");
const result = spawnSync(
  bun,
  [
    "install",
    "--frozen-lockfile",
    "--no-progress",
    "--backend=copyfile",
    ...request.workspaces.flatMap((p) => [
      "--filter",
      p === "." ? "./" : `./${p}`,
    ]),
  ],
  {
    cwd: workspace,
    stdio: "inherit",
    env: {
      PATH: join(repository, "tools"),
      HOME: join(repository, "scratch", "home"),
      TMPDIR: join(repository, "scratch", "tmp"),
      BUN_INSTALL_CACHE_DIR: join(repository, "scratch", "cache"),
      CI: "1",
      NO_COLOR: "1",
    },
  },
);
if (result.error) throw result.error;
if (result.status !== 0)
  throw new Error(
    `bun install failed: status=${result.status}, signal=${result.signal}`,
  );
for (const [path, hash] of Object.entries(before)) {
  if (digest(join(workspace, path)) !== hash)
    throw new Error(`Installer modified declared input: ${path}`);
}

const entries = [];
const files = [];
function visit(directory) {
  for (const name of readdirSync(join(workspace, directory)).sort()) {
    const path = directory ? `${directory}/${name}` : name;
    const absolute = join(workspace, path);
    const stat = lstatSync(absolute);
    if (stat.isSymbolicLink()) {
      const target = readlinkSync(absolute);
      const destination = relative(workspace, realpathSync(absolute));
      if (
        isAbsolute(target) ||
        destination === ".." ||
        destination.startsWith("../")
      )
        throw new Error(`Escaping package symlink: ${path} -> ${target}`);
      entries.push({ path, symlink: target });
    } else if (stat.isDirectory()) {
      entries.push({ path, directory: true });
      visit(path);
    } else if (stat.isFile()) {
      // A nested BUILD would make Bazel omit part of the acquired input tree.
      if (name === "BUILD" || name === "BUILD.bazel")
        throw new Error(
          `Unsupported package boundary in acquired files: ${path}`,
        );
      const mode = stat.mode & 0o111 ? 0o555 : 0o444;
      chmodSync(absolute, mode);
      entries.push({
        path,
        sha256: digest(absolute),
        executable: Boolean(mode & 0o111),
      });
      files.push(`workspace/${path}`);
    } else {
      throw new Error(`Unsupported acquired file type: ${path}`);
    }
  }
}
visit("");
writeFileSync(join(repository, "files.json"), JSON.stringify(files));
writeFileSync(
  join(repository, "closure.json"),
  JSON.stringify(
    {
      format: 1,
      installer: digest(import.meta.filename),
      tools: { bun: digest(bun), node: digest(node) },
      inputs: before,
      workspaces: request.workspaces,
      entries,
    },
    null,
    2,
  ) + "\n",
);
console.log(
  `Bun acquisition complete: ${files.length} files; content manifest: closure.json`,
);
