// The npm dependency graph the npm module extension declares repositories and
// graph targets from (docs/npm.md sec."Translation").
//
// bun.lock is JSONC, which Starlark's json.decode does not read, so this runs
// under the consumer's Bun and parses it with Bun's own parser. Its output is
// strict JSON, checked in beside the lock, and npm_graph's test fails when the
// checked-in copy differs from what this writes.
//
// `repositories` holds one tarball per locked release. Each `packages` entry
// is one node: a release, plus a suffix hashing its structure (see
// `canonical`) when it is a peer variant. Bun resolves a peer from the
// packages that depend on it, so one release can link different peers under
// different dependents, and its store keeps one directory for each. Nodes
// share an id exactly when their linked trees would be identical. A node
// carries:
// - `repository`: its release's tarball;
// - `edges`: each dependency name it resolves, its peers included, mapped to
//   the node it links;
// - `cycle`: present only on a node in a dependency cycle, naming the group
//   its members share, because Bazel targets cannot depend on one another in
//   a cycle.
// `workspaces` maps each workspace directory's dependency names to the nodes
// they link, or to `workspace:<directory>` for another workspace. `hoisted`
// maps each package name bunfig.toml's `hoistPattern` lets Bun's isolated store
// hoist to the node it links there (see `hoist`).

import { type LockMeta, readLock } from "./npm_lock";

const REGISTRY = "https://registry.npmjs.org";

type Repository = {
  name: string;
  version: string;
  url: string;
  integrity: string;
};

type GraphPackage = {
  repository: string;
  os?: string[];
  cpu?: string[];
  bin?: Record<string, string>;
  edges: Record<string, string>;
  cycle?: string;
};

type Graph = {
  repositories: Record<string, Repository>;
  packages: Record<string, GraphPackage>;
  workspaces: Record<string, Record<string, string>>;
  hoisted: Record<string, string>;
};

// An install path's segments, keeping a scope with its name.
function segments(path: string): string[] {
  const parts = path.split("/");
  const out: string[] = [];
  for (let i = 0; i < parts.length; i++) {
    out.push(parts[i].startsWith("@") ? `${parts[i]}/${parts[++i]}` : parts[i]);
  }
  return out;
}

function resolve(
  packages: Record<string, unknown>,
  from: string,
  dependency: string,
): string | undefined {
  const parents = segments(from);
  for (let depth = parents.length; depth >= 0; depth--) {
    const candidate = [...parents.slice(0, depth), dependency].join("/");
    if (candidate in packages) return candidate;
  }
  return undefined;
}

// The repository name Bazel accepts for a release; the release itself stays
// in the definition, so a collision between sanitized names fails below.
function repositoryName(name: string, version: string): string {
  return `npm__${name.replace(/^@/, "").replace(/\//g, "__")}__${version}`.replace(
    /[^A-Za-z0-9_.-]/g,
    "_",
  );
}

function tarballUrl(name: string, version: string): string {
  const base = name.includes("/") ? name.slice(name.indexOf("/") + 1) : name;
  return `${REGISTRY}/${name}/-/${base}-${version}.tgz`;
}

// Strongly connected components of size above one, each sorted, in the order
// of their first member.
function cycles(edges: Record<string, string[]>): string[][] {
  const index = new Map<string, number>();
  const low = new Map<string, number>();
  const stack: string[] = [];
  const onStack = new Set<string>();
  const found: string[][] = [];
  let next = 0;
  // Iterative Tarjan: a lock's chains can outrun a recursive walk's stack.
  for (const root of Object.keys(edges).sort()) {
    if (index.has(root)) continue;
    const work: [string, number][] = [[root, 0]];
    while (work.length > 0) {
      const frame = work[work.length - 1];
      const [node, child] = frame;
      if (child === 0) {
        index.set(node, next);
        low.set(node, next);
        next++;
        stack.push(node);
        onStack.add(node);
      }
      const targets = edges[node];
      if (child < targets.length) {
        frame[1]++;
        const target = targets[child];
        if (!index.has(target)) {
          work.push([target, 0]);
        } else if (onStack.has(target)) {
          low.set(node, Math.min(low.get(node)!, index.get(target)!));
        }
        continue;
      }
      work.pop();
      if (work.length > 0) {
        const parent = work[work.length - 1][0];
        low.set(parent, Math.min(low.get(parent)!, low.get(node)!));
      }
      if (low.get(node) === index.get(node)) {
        const component: string[] = [];
        let member: string;
        do {
          member = stack.pop()!;
          onStack.delete(member);
          component.push(member);
        } while (member !== node);
        if (component.length > 1) found.push(component.sort());
      }
    }
  }
  return found.sort((a, b) => (a[0] < b[0] ? -1 : 1));
}

type Entry = {
  path: string;
  name: string;
  repository: string;
  meta: LockMeta;
  // Dependency name to the install path Node's lookup finds from this path.
  deps: Record<string, string>;
  peers: string[];
};

function hash(value: unknown): string {
  return new Bun.CryptoHasher("sha256")
    .update(JSON.stringify(value))
    .digest("hex")
    .slice(0, 16);
}

// `hoistable` is bunfig.toml's `hoistPattern` (see `hoistPattern`).
export function translate(
  lockText: string,
  hoistable: readonly string[] = [],
): Graph {
  const lock = readLock(lockText);
  const members: Record<string, string> = {};
  const registry: Record<string, LockMeta> = {};
  const repositories: Record<string, Repository> = {};
  const entries: Record<string, Entry> = {};
  for (const [path, entry] of Object.entries(lock.packages)) {
    if (entry.kind === "workspace") {
      members[path] = entry.directory;
    } else {
      registry[path] = entry.meta;
    }
  }
  for (const path of Object.keys(registry).sort()) {
    const entry = lock.packages[path];
    if (entry.kind !== "registry") continue;
    const { name, version, integrity, meta } = entry;
    const repository = repositoryName(name, version);
    const existing = repositories[repository];
    if (
      existing &&
      (existing.name !== name ||
        existing.version !== version ||
        existing.integrity !== integrity)
    ) {
      throw new Error(
        `${path}: ${name}@${version} and ${existing.name}@${existing.version} share repository ${repository} with different releases`,
      );
    }
    repositories[repository] = {
      name,
      version,
      url: tarballUrl(name, version),
      integrity,
    };
    const deps = dependencies(registry, path, meta);
    entries[path] = {
      path,
      name,
      repository,
      meta,
      deps,
      peers: Object.keys(meta.peerDependencies)
        .filter((peer) => !(peer in deps))
        .sort(),
    };
  }

  // The peer names each install path's subtree takes from above it: its own
  // peers, and its dependencies' that it does not provide itself.
  const external: Record<string, Set<string>> = {};
  for (const entry of Object.values(entries)) {
    external[entry.path] = new Set(entry.peers);
  }
  for (let changed = true; changed;) {
    changed = false;
    for (const entry of Object.values(entries)) {
      const own = external[entry.path];
      for (const child of Object.values(entry.deps)) {
        for (const name of external[child]) {
          if (name in entry.deps || name === entry.name || own.has(name))
            continue;
          own.add(name);
          changed = true;
        }
      }
    }
  }

  const packages: Record<string, GraphPackage> = {};
  const origins: Record<string, string> = {};
  const building = new Set<string>();
  const compared = new Set<string>();
  const pending: [string, string, Map<string, string>][] = [];
  type Lookup = (name: string) => string | undefined;

  // The node for an install path under the ancestors `lookup` answers for,
  // by its build key. Its peers resolve from those ancestors, as Bun links
  // them. A peer no ancestor provides falls back to this package's own
  // lookup, which depends on where it is installed, so the key carries that
  // install path's answer too. `canonical` later merges keys whose nodes come
  // out identical.
  //
  // Every other install path that reaches a key must build the same edges, so
  // a component the key misses fails instead of merging distinct nodes. A
  // path reaching a node still being built is compared once the node is done.
  function instance(path: string, lookup: Lookup): string {
    const entry = entries[path];
    const release = repositories[entry.repository];
    const bound = new Map<string, string>();
    const bindings = [...external[path]].sort().map((name) => {
      const target = lookup(name);
      if (target !== undefined) {
        bound.set(name, target);
        return [name, target] as const;
      }
      const fallback = entry.peers.includes(name)
        ? resolve(registry, path, name)
        : undefined;
      return [
        name,
        fallback === undefined ? null : `path:${fallback}`,
      ] as const;
    });
    const id = `${release.name}@${release.version}${bindings.length === 0 ? "" : `+${hash(bindings)}`}`;
    if (id in packages) {
      const key = `${id}\0${path}`;
      if (origins[id] !== path && !compared.has(key)) {
        compared.add(key);
        if (building.has(id)) {
          pending.push([id, path, bound]);
        } else {
          compare(id, path, bound);
        }
      }
      return id;
    }
    origins[id] = path;
    packages[id] = { repository: entry.repository, edges: {} };
    building.add(id);
    packages[id] = build(entry, id, bound);
    building.delete(id);
    return id;
  }

  function compare(id: string, path: string, bound: Map<string, string>) {
    const node = build(entries[path], id, bound);
    if (JSON.stringify(packages[id]) !== JSON.stringify(node)) {
      throw new Error(
        `${origins[id]} and ${path} install ${id} with different edges`,
      );
    }
  }

  function build(
    entry: Entry,
    id: string,
    bound: Map<string, string>,
  ): GraphPackage {
    const children = childLookup(entry.deps, (name) =>
      name === entry.name ? id : bound.get(name),
    );
    const optionalPeers = new Set(entry.meta.optionalPeers);
    const edges: Record<string, string> = {};
    for (const name of [...Object.keys(entry.deps), ...entry.peers].sort()) {
      if (name in entry.deps) {
        edges[name] = children(name)!;
        continue;
      }
      let target = bound.get(name);
      if (target === undefined) {
        // With no ancestor providing it, Bun links the copy this package's
        // own lookup finds, an optional peer included.
        const fallback = resolve(registry, entry.path, name);
        if (fallback !== undefined) {
          target = instance(fallback, children);
        } else if (!optionalPeers.has(name)) {
          throw new Error(
            `${entry.path}: peer ${name} resolves to no locked package`,
          );
        }
      }
      if (target !== undefined) edges[name] = target;
    }
    const { os, cpu, bin } = entry.meta;
    return {
      repository: entry.repository,
      ...(os === undefined ? {} : { os }),
      ...(cpu === undefined ? {} : { cpu }),
      ...(bin === undefined ? {} : { bin }),
      edges,
    };
  }

  // What a package's dependencies see: each other, as nodes under the same
  // parent, and whatever the parent's own ancestors answer.
  function childLookup(deps: Record<string, string>, above: Lookup): Lookup {
    const resolved = new Map<string, string>();
    const resolving = new Set<string>();
    const lookup: Lookup = (name) => {
      if (!(name in deps)) return above(name);
      const done = resolved.get(name);
      if (done !== undefined) return done;
      if (resolving.has(name)) {
        throw new Error(
          `${[...resolving].join(", ")}: sibling packages peer on each other, which no node identity can express`,
        );
      }
      resolving.add(name);
      const id = instance(deps[name], lookup);
      resolving.delete(name);
      resolved.set(name, id);
      return id;
    };
    return lookup;
  }

  // Bun keys a workspace's own copy of a dependency under the workspace's
  // install path, its package name, so `<name>/<dependency>`.
  const memberPaths: Record<string, string> = {};
  for (const [path, directory] of Object.entries(members)) {
    memberPaths[directory] = path;
  }
  const workspaces: Record<string, Record<string, string>> = {};
  for (const [directory, workspace] of Object.entries(lock.workspaces)) {
    const local: Record<string, string> = {};
    const deps: Record<string, string> = {};
    const wanted = {
      ...workspace.dependencies,
      ...workspace.devDependencies,
      ...workspace.optionalDependencies,
    };
    const own = directory ? memberPaths[directory] : undefined;
    for (const name of Object.keys(wanted).sort()) {
      if (name in members) {
        local[name] = `workspace:${members[name]}`;
      } else if (own !== undefined && `${own}/${name}` in registry) {
        deps[name] = `${own}/${name}`;
      } else if (name in registry) {
        deps[name] = name;
      } else if (!(name in workspace.optionalDependencies)) {
        throw new Error(
          `workspace ${directory || "."}: dependency ${name} resolves to no locked package`,
        );
      }
    }
    const roots = childLookup(deps, () => undefined);
    const edges: Record<string, string> = { ...local };
    for (const name of Object.keys(deps)) edges[name] = roots(name)!;
    workspaces[directory || "."] = Object.fromEntries(
      Object.entries(edges).sort(([a], [b]) => (a < b ? -1 : 1)),
    );
  }

  // The node each top-level install path builds with no dependent above it,
  // which `hoist` compares with the variants the workspaces install.
  const topLevel: Record<string, string> = {};
  for (const path of Object.keys(entries).sort()) {
    if (segments(path).length !== 1 || entries[path].name !== path) continue;
    topLevel[path] = instance(path, () => undefined);
  }

  for (let next = pending.pop(); next; next = pending.pop()) compare(...next);

  const final = canonical(packages, repositories);
  const rename = (id: string) => final.get(id) ?? id;
  for (const edges of Object.values(workspaces)) {
    for (const name of Object.keys(edges)) edges[name] = rename(edges[name]);
  }
  const sortedPackages: Record<string, GraphPackage> = {};
  for (const [id, node] of Object.entries(packages)) {
    const edges = Object.fromEntries(
      Object.entries(node.edges).map(([name, target]) => [
        name,
        rename(target),
      ]),
    );
    const merged = { ...node, edges };
    const existing = sortedPackages[rename(id)];
    // The merge's own guarantee, checked where a violation would otherwise
    // overwrite a node: nodes sharing a final id have the same edges.
    if (existing && JSON.stringify(existing) !== JSON.stringify(merged)) {
      throw new Error(
        `${id} merges into ${rename(id)} with different edges than the node already there`,
      );
    }
    sortedPackages[rename(id)] = merged;
  }
  // Only what the workspaces reach is installed; a node only a top-level
  // comparison built is not.
  const reached = new Set<string>();
  const stack = Object.values(workspaces).flatMap((edges) =>
    Object.values(edges).filter((target) => !target.startsWith("workspace:")),
  );
  for (let id = stack.pop(); id !== undefined; id = stack.pop()) {
    if (reached.has(id)) continue;
    reached.add(id);
    stack.push(...Object.values(sortedPackages[id].edges));
  }
  for (const id of Object.keys(sortedPackages).sort()) {
    const node = sortedPackages[id];
    delete sortedPackages[id];
    if (reached.has(id)) sortedPackages[id] = node;
  }
  for (const path of Object.keys(topLevel))
    topLevel[path] = rename(topLevel[path]);
  const targets: Record<string, string[]> = {};
  for (const [id, node] of Object.entries(sortedPackages)) {
    targets[id] = [...new Set(Object.values(node.edges))];
  }
  for (const component of cycles(targets)) {
    for (const member of component) sortedPackages[member].cycle = component[0];
  }

  const hoisted = hoist(entries, sortedPackages, topLevel, hoistable);

  const sortedRepositories: Record<string, Repository> = {};
  for (const name of Object.keys(repositories).sort())
    sortedRepositories[name] = repositories[name];
  return {
    repositories: sortedRepositories,
    packages: sortedPackages,
    workspaces,
    hoisted,
  };
}

// What Bun's isolated store links into its fallback directory, which a
// package's own lookup reaches after its edges, so a package that requires
// something it does not declare finds it there: for each name `hoistable`
// allows, an installed node of its top-level release. A release with a single
// installed node hoists it. A release installed under several peer variants
// hoists the variant its top-level path builds with no dependent above it; a
// lock where that is none of them fails rather than guessing. So does a name
// with no top-level release the workspaces install, since Bun hoists nothing
// for it and the entry allowing it is stale.
function hoist(
  entries: Record<string, Entry>,
  packages: Record<string, GraphPackage>,
  topLevel: Record<string, string>,
  hoistable: readonly string[],
): Record<string, string> {
  const variants: Record<string, string[]> = {};
  for (const [id, node] of Object.entries(packages)) {
    (variants[node.repository] ??= []).push(id);
  }
  const hoisted: Record<string, string> = {};
  for (const path of [...hoistable].sort()) {
    const installed =
      path in topLevel ? (variants[entries[path].repository] ?? []) : [];
    if (installed.length === 0) {
      throw new Error(
        `hoistPattern names ${path}, which no workspace installs at the top level`,
      );
    } else if (installed.length === 1) {
      hoisted[path] = installed[0];
    } else {
      if (!installed.includes(topLevel[path])) {
        throw new Error(
          `${path}: its top-level node ${topLevel[path]} is none of its installed variants ${installed.join(", ")}, so which one Bun hoists is unknown`,
        );
      }
      hoisted[path] = topLevel[path];
    }
  }
  return hoisted;
}

// The final id of each node built under its install-path key. That key keeps
// install paths with different fallback peers apart while the graph is built,
// but it can also split nodes whose trees come out identical, so equivalent
// nodes merge. Deciding equality and naming are separate steps, because
// equality needs the whole graph and a name must not.
//
// Equality: one partition of every node, refined on exact keys (metadata,
// then each edge's target class) until it stops splitting. Each key carries
// the node's own previous class, so each pass refines the last and an
// unchanged count means an unchanged partition. The result is the coarsest
// partition in which equal nodes have equal metadata and edges to equal
// nodes, across cycles and everywhere else; no digest decides it.
//
// Naming: each class's id comes from a walk of the merged graph from that
// class, visiting edges in name order. A package has at most one target per
// dependency name, so the walk describes the class completely and depends
// only on what the class reaches; a change elsewhere never renames it. A
// class keeps the bare `name@version` when any of its members was built with
// no peer variation, which only an install of that same release can change;
// otherwise its id is `name@version+` a digest of the walk. Distinct classes
// landing on one id fail.
export function canonical(
  packages: Record<string, GraphPackage>,
  repositories: Record<string, Repository>,
): Map<string, string> {
  const ids = Object.keys(packages).sort();
  const metadata = (id: string) => {
    const { repository, os, cpu, bin } = packages[id];
    return [repository, os ?? null, cpu ?? null, bin ?? null];
  };
  const sortedEdges = (id: string) =>
    Object.entries(packages[id].edges).sort(([a], [b]) => (a < b ? -1 : 1));
  const rank = (keys: Map<string, string>) => {
    const distinct = [...new Set(keys.values())].sort();
    const index = new Map(distinct.map((key, i) => [key, i]));
    return new Map(ids.map((id) => [id, index.get(keys.get(id)!)!]));
  };

  let classOf = rank(
    new Map(ids.map((id) => [id, JSON.stringify(metadata(id))])),
  );
  for (let count = new Set(classOf.values()).size; ;) {
    const next = rank(
      new Map(
        ids.map((id) => [
          id,
          JSON.stringify([
            classOf.get(id),
            sortedEdges(id).map(([name, target]) => [
              name,
              classOf.get(target),
            ]),
          ]),
        ]),
      ),
    );
    const refined = new Set(next.values()).size;
    classOf = next;
    if (refined === count) break;
    count = refined;
  }

  // The merged graph: a class's metadata and edges are any member's, since
  // the partition gives every member the same.
  const membersOf = new Map<number, string[]>();
  for (const id of ids) {
    const members = membersOf.get(classOf.get(id)!) ?? [];
    members.push(id);
    membersOf.set(classOf.get(id)!, members);
  }
  const representative = (cls: number) => membersOf.get(cls)![0];

  const walk = (root: number) => {
    const order = [root];
    const visited = new Map([[root, 0]]);
    const form: unknown[] = [];
    for (let i = 0; i < order.length; i++) {
      const id = representative(order[i]);
      const edges = sortedEdges(id).map(([name, target]) => {
        const cls = classOf.get(target)!;
        if (!visited.has(cls)) {
          visited.set(cls, order.length);
          order.push(cls);
        }
        return [name, visited.get(cls)];
      });
      form.push([metadata(id), edges]);
    }
    return form;
  };

  const finalOf = new Map<number, string>();
  const claimed = new Map<string, number>();
  for (const [cls, members] of membersOf) {
    const { name, version } = repositories[packages[members[0]].repository];
    const release = `${name}@${version}`;
    const final = members.includes(release)
      ? release
      : `${release}+${hash(walk(cls))}`;
    const owner = claimed.get(final);
    if (owner !== undefined) {
      throw new Error(
        `${representative(owner)} and ${members[0]} are different nodes that canonicalize to the same id ${final}`,
      );
    }
    claimed.set(final, cls);
    finalOf.set(cls, final);
  }
  return new Map(ids.map((id) => [id, finalOf.get(classOf.get(id)!)!]));
}

// A package's dependencies and the resolvable optional ones, by Node's lookup
// from its install path. A name it also peers on is a dependency.
function dependencies(
  registry: Record<string, unknown>,
  path: string,
  meta: LockMeta,
): Record<string, string> {
  const optional = new Set(Object.keys(meta.optionalDependencies));
  const deps: Record<string, string> = {};
  for (const name of [
    ...new Set([...Object.keys(meta.dependencies), ...optional]),
  ].sort()) {
    const resolved = resolve(registry, path, name);
    if (resolved !== undefined) {
      deps[name] = resolved;
    } else if (!optional.has(name)) {
      // Bun locks every resolvable optional dependency; one it omits was
      // skipped at lock time, but a required edge with nothing behind it
      // is a lock this graph would silently under-link.
      throw new Error(
        `${path}: dependency ${name} resolves to no locked package`,
      );
    }
  }
  return deps;
}

// bunfig.toml's `hoistPattern`. Bun matches each entry as a pnpm-style glob,
// with `!` excluding; the graph models only literal package names, so any
// other entry fails here rather than hoisting a set `hoist` cannot name.
export function hoistPattern(bunfigText: string): string[] {
  const install = (Bun.TOML.parse(bunfigText) as { install?: unknown })
    .install as { hoistPattern?: unknown } | undefined;
  const pattern = install?.hoistPattern;
  if (
    !Array.isArray(pattern) ||
    !pattern.every((name) => typeof name === "string")
  ) {
    throw new Error(
      "bunfig.toml: install.hoistPattern must be a list of package names; without it Bun hoists every name",
    );
  }
  for (const name of pattern) {
    if (!/^(@[a-z0-9._~-]+\/)?[a-z0-9._~-]+$/.test(name)) {
      throw new Error(
        `bunfig.toml: hoistPattern entry ${JSON.stringify(name)} is not a literal package name`,
      );
    }
  }
  return pattern;
}

// One entry per line, so a bump reads in review as the lines of the packages
// it changed.
export function render(graph: Graph): string {
  const section = (entries: Record<string, unknown>) =>
    Object.entries(entries)
      .map(
        ([key, value]) =>
          `    ${JSON.stringify(key)}: ${JSON.stringify(value)}`,
      )
      .join(",\n");
  return `{\n  "repositories": {\n${section(graph.repositories)}\n  },\n  "packages": {\n${section(graph.packages)}\n  },\n  "workspaces": {\n${section(graph.workspaces)}\n  },\n  "hoisted": {\n${section(graph.hoisted)}\n  }\n}\n`;
}

if (import.meta.main) {
  const [lockPath, bunfigPath, outPath] = Bun.argv.slice(2);
  if (!lockPath || !bunfigPath || !outPath) {
    throw new Error(
      "usage: npm_graph.ts <bun.lock> <bunfig.toml> <output.json>",
    );
  }
  const hoistable = hoistPattern(await Bun.file(bunfigPath).text());
  await Bun.write(
    outPath,
    render(translate(await Bun.file(lockPath).text(), hoistable)),
  );
}
