// The dependency bin links in Bun's isolated store that its install threads
// decide, made a function of the lock
// (https://github.com/curlewlabs-com/rules_bun/issues/16).
import {
  chmodSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  realpathSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { join, relative, resolve, sep } from "node:path";

// The lock stores a package's bins as Bun's linker reads them: `bin` is a
// file linked under the name its dependent declared, or a map of names to
// files, and `binDir` names a directory whose entries are all bins.
function binLinks(declared, alias, packageDirectory) {
  if (typeof declared.bin === "string") {
    if (!declared.bin) return [];
    const unscoped = alias.startsWith("@")
      ? alias.slice(alias.indexOf("/") + 1)
      : alias;
    return [[unscoped, declared.bin]];
  }
  if (declared.bin !== undefined) {
    return Object.entries(declared.bin)
      .map(([name, target]) => [
        name.slice(
          Math.max(
            name.lastIndexOf("/"),
            name.lastIndexOf("\\"),
            name.lastIndexOf(":"),
          ) + 1,
        ),
        target,
      ])
      .filter(([name, target]) => name && target);
  }
  if (!declared.binDir) return [];
  const directory = join(packageDirectory, declared.binDir);
  if (!existsSync(directory)) return [];
  return readdirSync(directory, { withFileTypes: true })
    .filter((entry) => entry.isFile() || entry.isSymbolicLink())
    .map((entry) => [entry.name, join(declared.binDir, entry.name)]);
}

// Bun removes a carriage return ending a bin's shebang line when it links the
// bin, reading at most this much of the file to find that line.
const SHEBANG_SCAN_BYTES = 2048;

function normalizeShebang(path) {
  const contents = readFileSync(path);
  const newline = contents.subarray(0, SHEBANG_SCAN_BYTES).indexOf(0x0a);
  if (contents.length < 5 || contents[0] !== 0x23 || contents[1] !== 0x21)
    return;
  if (newline < 0 || contents[newline - 1] !== 0x0d) return;
  writeFileSync(
    path,
    Buffer.concat([
      contents.subarray(0, newline - 1),
      contents.subarray(newline),
    ]),
  );
}

// Strongly connected components of the store's dependency graph, iteratively:
// a long dependency chain would exhaust the call stack of a recursive walk.
function components(graph) {
  const index = new Map();
  const low = new Map();
  const stack = [];
  const onStack = new Set();
  const component = new Map();
  const visit = (node) => {
    index.set(node, index.size);
    low.set(node, index.get(node));
    stack.push(node);
    onStack.add(node);
  };
  for (const start of graph.keys()) {
    if (index.has(start)) continue;
    visit(start);
    const work = [[start, 0]];
    while (work.length) {
      const frame = work.at(-1);
      const [node, next] = frame;
      const children = graph.get(node);
      if (next < children.length) {
        frame[1] += 1;
        const child = children[next];
        if (!index.has(child)) {
          visit(child);
          work.push([child, 0]);
        } else if (onStack.has(child)) {
          low.set(node, Math.min(low.get(node), index.get(child)));
        }
        continue;
      }
      work.pop();
      if (work.length) {
        const parent = work.at(-1)[0];
        low.set(parent, Math.min(low.get(parent), low.get(node)));
      }
      if (low.get(node) === index.get(node)) {
        let member;
        do {
          member = stack.pop();
          onStack.delete(member);
          component.set(member, node);
        } while (member !== node);
      }
    }
  }
  return component;
}

/**
 * Bun links each dependency's bins into a store package's node_modules/.bin
 * once that dependency is installed, but does not wait for a dependency in a
 * cycle with the package, and skips a bin whose file is not there yet. Install
 * threads then decide whether the link exists: eslint and the
 * @eslint-community/eslint-utils that peers on it are one such pair. This adds
 * every link Bun can skip that way, as Bun writes it when the dependency is
 * installed first, and returns how many it added.
 *
 * `bins` maps `name@version` to the package's `bin` or `binDir` from the lock.
 * Only store packages are considered: a registry package cannot depend on a
 * workspace, so a cycle through one never reaches the store.
 */
export function completeCyclicBins(workspace, bins) {
  const store = join(workspace, "node_modules", ".bun");
  if (!existsSync(store)) return 0;
  const storeRoot = realpathSync(store) + sep;
  const dependencies = new Map();
  // The store's own fallback directory holds links, not a package.
  for (const entry of readdirSync(store).filter(
    (name) => name !== "node_modules",
  )) {
    const modules = join(store, entry, "node_modules");
    const aliases = readdirSync(modules)
      .filter((name) => name !== ".bin")
      .flatMap((name) =>
        name.startsWith("@")
          ? readdirSync(join(modules, name)).map((child) => `${name}/${child}`)
          : [name],
      )
      // The package itself is a directory; its dependencies are links.
      .filter((alias) => lstatSync(join(modules, alias)).isSymbolicLink())
      // Bun links a package's dependencies in this order, and the first to
      // claim a bin name keeps it.
      .sort((left, right) => (left < right ? -1 : left > right ? 1 : 0));
    dependencies.set(
      entry,
      aliases.map((alias) => {
        const target = realpathSync(join(modules, alias));
        const inStore = target.startsWith(storeRoot);
        return {
          alias,
          entry: inStore ? target.slice(storeRoot.length).split(sep)[0] : null,
        };
      }),
    );
  }
  const component = components(
    new Map(
      [...dependencies].map(([entry, edges]) => [
        entry,
        edges.flatMap(({ entry: dependency }) =>
          dependency !== null && dependencies.has(dependency)
            ? [dependency]
            : [],
        ),
      ]),
    ),
  );
  let added = 0;
  for (const [entry, edges] of dependencies) {
    const cyclic = (dependency) =>
      dependency !== null &&
      dependency !== entry &&
      component.get(dependency) === component.get(entry);
    if (!edges.some(({ entry: dependency }) => cyclic(dependency))) continue;
    const modules = join(store, entry, "node_modules");
    const binDirectory = join(modules, ".bin");
    const claimed = new Set();
    for (const { alias, entry: dependency } of edges) {
      const packageDirectory = join(modules, alias);
      const manifest = JSON.parse(
        readFileSync(join(packageDirectory, "package.json"), "utf8"),
      );
      const declared = bins[`${manifest.name}@${manifest.version}`];
      if (declared === undefined) continue;
      for (const [name, target] of binLinks(
        declared,
        alias,
        packageDirectory,
      )) {
        if (claimed.has(name)) continue;
        claimed.add(name);
        const destination = join(binDirectory, name);
        const file = resolve(packageDirectory, target);
        if (
          !cyclic(dependency) ||
          lstatExists(destination) ||
          !existsSync(file)
        )
          continue;
        mkdirSync(binDirectory, { recursive: true });
        symlinkSync(relative(binDirectory, file), destination);
        // As Bun's linker leaves a bin it links.
        chmodSync(file, 0o777);
        normalizeShebang(file);
        added += 1;
      }
    }
  }
  return added;
}

function lstatExists(path) {
  try {
    lstatSync(path);
    return true;
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}
