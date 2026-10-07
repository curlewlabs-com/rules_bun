// Runs under the declared Bun so the committed lock is read by Bun's own parser.
import { writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const repository = resolve(process.argv[2]);
const lock = Bun.JSONC.parse(
  await Bun.file(join(repository, "workspace", "bun.lock")).text(),
);
if (lock === null || typeof lock !== "object" || Array.isArray(lock)) {
  throw new Error("bun.lock is not an object");
}
const packages = lock.packages ?? {};
if (packages === null || typeof packages !== "object" || Array.isArray(packages)) {
  throw new Error("bun.lock packages is not an object");
}

// npm os/cpu semantics: a listed negation excludes, and any positive entry is
// an allow-list. Admitting too much costs a download; admitting too little
// fails installation against the loopback registry, never a silent fetch.
function admits(constraint, value) {
  if (constraint === undefined) return true;
  const items = typeof constraint === "string" ? [constraint] : constraint;
  if (!Array.isArray(items) || items.some((item) => typeof item !== "string")) {
    throw new Error(`Unreadable platform constraint: ${JSON.stringify(constraint)}`);
  }
  if (items.includes(`!${value}`)) return false;
  const allowed = items.filter((item) => !item.startsWith("!"));
  return allowed.length === 0 || allowed.includes(value);
}

const PACKAGE_NAME = /^(@[A-Za-z0-9~-][A-Za-z0-9._~-]*\/)?[A-Za-z0-9~-][A-Za-z0-9._~-]*$/;

// A lock key is the dependency path to a package, so its last name is the one
// the dependent declared; for an alias, that differs from the package's name.
function declaredName(key) {
  const parts = key.split("/");
  return parts.length > 1 && parts.at(-2).startsWith("@")
    ? parts.slice(-2).join("/")
    : parts.at(-1);
}

function binDeclaration(spec, metadata) {
  const { bin, binDir } = metadata;
  if (bin === undefined && binDir === undefined) return undefined;
  const readable =
    bin === undefined
      ? typeof binDir === "string"
      : typeof bin === "string" ||
        (bin !== null &&
          typeof bin === "object" &&
          !Array.isArray(bin) &&
          Object.values(bin).every((target) => typeof target === "string"));
  if (!readable) throw new Error(`Unreadable bin declaration: ${spec}`);
  return bin === undefined ? { binDir } : { bin };
}

const tarballs = new Map();
const aliases = new Set();
const bins = {};
for (const [key, entry] of Object.entries(packages)) {
  if (!Array.isArray(entry) || typeof entry[0] !== "string") {
    throw new Error(`Unreadable lock entry: ${key}`);
  }
  const spec = entry[0];
  const at = spec.lastIndexOf("@");
  const name = spec.slice(0, at);
  const version = spec.slice(at + 1);
  if (at <= 0 || !name || !version) throw new Error(`Unreadable lock spec: ${spec}`);
  if (version.startsWith("workspace:") && entry.length === 1) continue;
  // Default-registry entries are [spec, "", metadata, integrity]; every other
  // source would reach the network outside Bazel's verified downloads.
  const [, registry, metadata, integrity] = entry;
  if (
    entry.length !== 4 ||
    registry !== "" ||
    metadata === null ||
    typeof metadata !== "object" ||
    Array.isArray(metadata) ||
    version.includes(":") ||
    typeof integrity !== "string" ||
    !/^sha(256|384|512)-[A-Za-z0-9+/]+={0,2}$/.test(integrity)
  ) {
    throw new Error(
      `Unsupported lock entry (only default-registry packages with integrity are acquired): ${key}`,
    );
  }
  // The name and version become a download path, so neither may leave it.
  if (!PACKAGE_NAME.test(name) || !/^[A-Za-z0-9][A-Za-z0-9.+_-]*$/.test(version)) {
    throw new Error(`Unsupported package name or version: ${spec}`);
  }
  // An alias name becomes a line of the installer's Bun configuration.
  const declared = declaredName(key);
  if (!PACKAGE_NAME.test(declared)) throw new Error(`Unsupported dependency name: ${key}`);
  if (declared !== name) aliases.add(declared);
  const declaration = binDeclaration(spec, metadata);
  if (declaration !== undefined) {
    const previous = bins[spec];
    if (previous !== undefined && JSON.stringify(previous) !== JSON.stringify(declaration)) {
      throw new Error(`Conflicting bin declarations for ${spec}`);
    }
    bins[spec] = declaration;
  }
  if (!admits(metadata.os, process.platform) || !admits(metadata.cpu, process.arch)) {
    continue;
  }
  const path = `${name}/-/${name.split("/").pop()}-${version}.tgz`;
  const previous = tarballs.get(path);
  if (previous !== undefined && previous !== integrity) {
    throw new Error(`Conflicting integrity for ${path}`);
  }
  tarballs.set(path, integrity);
}

writeFileSync(
  join(repository, "tarballs.json"),
  JSON.stringify(
    [...tarballs.entries()]
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
      .map(([path, integrity]) => ({ path, integrity })),
  ),
);
// bun/layout.mjs and the installer read what the lock decides about the layout.
writeFileSync(
  join(repository, "layout.json"),
  JSON.stringify({ aliases: [...aliases].sort(), bins }),
);
console.log(`Planned ${tarballs.size} registry tarballs for Bazel acquisition`);
