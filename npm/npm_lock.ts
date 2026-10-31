// The fields of bun.lock the npm graph translator reads, narrowed from what
// Bun's JSONC parser returns. A field this does not name is ignored; a named
// field of the wrong shape throws, naming where it sits in the lock, rather
// than becoming a wrong graph.

export type LockMeta = {
  dependencies: Record<string, string>;
  optionalDependencies: Record<string, string>;
  peerDependencies: Record<string, string>;
  optionalPeers: string[];
  os?: string[];
  cpu?: string[];
  bin?: Record<string, string>;
};

export type LockPackage =
  | { kind: "workspace"; directory: string }
  | {
      kind: "registry";
      name: string;
      version: string;
      integrity: string;
      meta: LockMeta;
    };

export type LockWorkspace = {
  dependencies: Record<string, string>;
  devDependencies: Record<string, string>;
  optionalDependencies: Record<string, string>;
};

export type Lock = {
  workspaces: Record<string, LockWorkspace>;
  packages: Record<string, LockPackage>;
};

function object(value: unknown, where: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`bun.lock ${where}: expected an object`);
  }
  return value as Record<string, unknown>;
}

function stringMap(value: unknown, where: string): Record<string, string> {
  if (value === undefined) return {};
  const map = object(value, where);
  for (const [key, entry] of Object.entries(map)) {
    if (typeof entry !== "string") {
      throw new Error(`bun.lock ${where}.${key}: expected a string`);
    }
  }
  return map as Record<string, string>;
}

function strings(value: unknown, where: string): string[] | undefined {
  if (value === undefined) return undefined;
  const list = typeof value === "string" ? [value] : value;
  if (!Array.isArray(list) || !list.every((item) => typeof item === "string")) {
    throw new Error(
      `bun.lock ${where}: expected a string or a list of strings`,
    );
  }
  return list;
}

function meta(value: unknown, name: string, where: string): LockMeta {
  const fields = value === undefined ? {} : object(value, where);
  const bin = fields.bin;
  return {
    dependencies: stringMap(fields.dependencies, `${where}.dependencies`),
    optionalDependencies: stringMap(
      fields.optionalDependencies,
      `${where}.optionalDependencies`,
    ),
    peerDependencies: stringMap(
      fields.peerDependencies,
      `${where}.peerDependencies`,
    ),
    optionalPeers:
      strings(fields.optionalPeers, `${where}.optionalPeers`) ?? [],
    os: strings(fields.os, `${where}.os`),
    cpu: strings(fields.cpu, `${where}.cpu`),
    // A string bin is npm's shorthand for one bin named after the package,
    // without its scope.
    bin:
      typeof bin === "string"
        ? { [name.slice(name.indexOf("/") + 1)]: bin }
        : bin === undefined
          ? undefined
          : stringMap(bin, `${where}.bin`),
  };
}

function lockPackage(value: unknown, where: string): LockPackage {
  if (!Array.isArray(value) || typeof value[0] !== "string") {
    throw new Error(
      `bun.lock ${where}: expected a list starting with its spec`,
    );
  }
  const spec = value[0];
  // Workspace members are the consumer's own sources, not downloads.
  const member = /@workspace:(.*)$/.exec(spec);
  if (member) return { kind: "workspace", directory: member[1] };
  const [, source, fields, integrity] = value;
  if (source !== "" || value.length !== 4) {
    throw new Error(
      `bun.lock ${where}: only default-registry packages are translated, found ${JSON.stringify(value.slice(0, 2))}`,
    );
  }
  const at = spec.lastIndexOf("@");
  const name = spec.slice(0, at);
  const version = spec.slice(at + 1);
  if (
    at <= 0 ||
    !version ||
    typeof integrity !== "string" ||
    !integrity.startsWith("sha512-")
  ) {
    throw new Error(
      `bun.lock ${where}: unreadable lock entry ${JSON.stringify(spec)}`,
    );
  }
  return {
    kind: "registry",
    name,
    version,
    integrity,
    meta: meta(fields, name, where),
  };
}

export function readLock(text: string): Lock {
  const lock = object(Bun.JSONC.parse(text), "root");
  if (lock.lockfileVersion !== 1) {
    throw new Error(
      `bun.lock lockfileVersion ${JSON.stringify(lock.lockfileVersion)} is not the version this translator reads (1)`,
    );
  }
  const workspaces: Record<string, LockWorkspace> = {};
  for (const [directory, value] of Object.entries(
    lock.workspaces === undefined ? {} : object(lock.workspaces, "workspaces"),
  )) {
    const where = `workspaces[${JSON.stringify(directory)}]`;
    const fields = object(value, where);
    workspaces[directory] = {
      dependencies: stringMap(fields.dependencies, `${where}.dependencies`),
      devDependencies: stringMap(
        fields.devDependencies,
        `${where}.devDependencies`,
      ),
      optionalDependencies: stringMap(
        fields.optionalDependencies,
        `${where}.optionalDependencies`,
      ),
    };
  }
  const packages: Record<string, LockPackage> = {};
  for (const [path, value] of Object.entries(
    lock.packages === undefined ? {} : object(lock.packages, "packages"),
  )) {
    packages[path] = lockPackage(value, `packages[${JSON.stringify(path)}]`);
  }
  return { workspaces, packages };
}
