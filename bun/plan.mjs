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

const tarballs = new Map();
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
  if (
    !/^(@[A-Za-z0-9~-][A-Za-z0-9._~-]*\/)?[A-Za-z0-9~-][A-Za-z0-9._~-]*$/.test(name) ||
    !/^[A-Za-z0-9][A-Za-z0-9.+_-]*$/.test(version)
  ) {
    throw new Error(`Unsupported package name or version: ${spec}`);
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
console.log(`Planned ${tarballs.size} registry tarballs for Bazel acquisition`);
