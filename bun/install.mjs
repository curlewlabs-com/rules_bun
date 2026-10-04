import { createHash } from "node:crypto";
import {
  chmodSync,
  createReadStream,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  readlinkSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { createServer } from "node:http";
import { createServer as createSocketServer } from "node:net";
import { isAbsolute, join, relative, resolve } from "node:path";
import { spawn } from "node:child_process";

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

// Bun fetches only the tarballs Bazel already downloaded and verified.
const registry = join(repository, "registry");
const planned = new Set(
  JSON.parse(readFileSync(join(repository, "tarballs.json"), "utf8")).map(
    (tarball) => tarball.path,
  ),
);
const unplanned = [];
const server = createServer((incoming, response) => {
  const path = decodeURIComponent(
    new URL(incoming.url, "http://registry").pathname,
  ).slice(1);
  if (incoming.method !== "GET" || !planned.has(path)) {
    unplanned.push(`${incoming.method} ${incoming.url}`);
    response.writeHead(404).end();
    return;
  }
  response.writeHead(200, { "content-type": "application/octet-stream" });
  createReadStream(join(registry, path)).pipe(response);
});
// Proxied traffic is the installer's other route out; refuse and record it.
const proxied = [];
const proxy = createSocketServer((socket) => {
  proxied.push(socket.remoteAddress);
  socket.destroy();
});
const listen = (target) =>
  new Promise((done, failed) => {
    target.once("error", failed);
    target.listen(0, "127.0.0.1", () => done(target.address().port));
  });
const registryPort = await listen(server);
const proxyAddress = `http://127.0.0.1:${await listen(proxy)}`;
let status, signal;
try {
  ({ status, signal } = await new Promise((done, failed) => {
    const child = spawn(
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
          BUN_CONFIG_REGISTRY: `http://127.0.0.1:${registryPort}/`,
          HTTP_PROXY: proxyAddress,
          HTTPS_PROXY: proxyAddress,
          http_proxy: proxyAddress,
          https_proxy: proxyAddress,
          NO_PROXY: "127.0.0.1",
          no_proxy: "127.0.0.1",
          CI: "1",
          NO_COLOR: "1",
        },
      },
    );
    child.once("error", failed);
    child.once("close", (code, killed) => done({ status: code, signal: killed }));
  }));
} finally {
  server.close();
  proxy.close();
}
// Checked first: an install that also failed usually failed because of it, and
// one that survived a refused request still reached outside its inputs.
if (unplanned.length || proxied.length)
  throw new Error(
    `Installation reached outside the locked tarballs: ${JSON.stringify({ unplanned, proxied, status, signal })}`,
  );
if (status !== 0)
  throw new Error(`bun install failed: status=${status}, signal=${signal}`);
// The verified tarballs stay in Bazel's repository cache, not in every closure.
rmSync(registry, { recursive: true, force: true });
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
      planner: digest(join(import.meta.dirname, "plan.mjs")),
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
