// Write the acquired workspace as one reproducible tar, so a consumer can
// declare the closure as a single input instead of one input per file
// (docs/closure-format.md, "Closure archive").
//
// The same entries give the same bytes on every host: entries in the order
// given, which the installer sorts; fixed times, owners and modes; no
// compression, whose output can vary with the zlib build. Paths and link
// targets too long for the ustar fields go in a PAX extended header, the
// POSIX.1-2001 form GNU tar and bsdtar both read.
import { closeSync, openSync, readFileSync, writeSync } from "node:fs";

const BLOCK = 512;

function octal(value, width) {
  // ustar numeric fields are zero-padded octal ending in a NUL.
  const text = value.toString(8);
  if (text.length > width - 1)
    throw new Error(`tar field overflow: ${value} in ${width} bytes`);
  return text.padStart(width - 1, "0") + "\0";
}

function header({ name, mode, size, type, linkname = "", prefix = "" }) {
  const block = Buffer.alloc(BLOCK);
  const put = (text, offset, width) => {
    const bytes = Buffer.from(text, "utf8");
    if (bytes.length > width)
      throw new Error(`tar field too long: ${text} in ${width} bytes`);
    bytes.copy(block, offset);
  };
  put(name, 0, 100);
  put(octal(mode, 8), 100, 8);
  put(octal(0, 8), 108, 8); // uid
  put(octal(0, 8), 116, 8); // gid
  put(octal(size, 12), 124, 12);
  put(octal(0, 12), 136, 12); // mtime
  block.fill(0x20, 148, 156); // checksum placeholder: eight spaces
  put(type, 156, 1);
  put(linkname, 157, 100);
  put("ustar\0", 257, 6);
  put("00", 263, 2);
  put(prefix, 345, 155);
  let sum = 0;
  for (const byte of block) sum += byte;
  put(octal(sum, 7) + " ", 148, 8);
  return block;
}

function padding(size) {
  const remainder = size % BLOCK;
  return remainder ? Buffer.alloc(BLOCK - remainder) : Buffer.alloc(0);
}

// A PAX record is "<length> <key>=<value>\n", where length counts itself.
function paxRecord(key, value) {
  const body = ` ${key}=${value}\n`;
  let length = Buffer.byteLength(body, "utf8");
  while (String(length).length + Buffer.byteLength(body, "utf8") !== length)
    length = String(length).length + Buffer.byteLength(body, "utf8");
  return `${length}${body}`;
}

// The ustar name/prefix split, or null when the path needs a PAX record.
function ustarName(path) {
  if (Buffer.byteLength(path, "utf8") <= 100) return { name: path, prefix: "" };
  for (let cut = path.indexOf("/"); cut !== -1; cut = path.indexOf("/", cut + 1)) {
    const prefix = path.slice(0, cut);
    const name = path.slice(cut + 1);
    if (Buffer.byteLength(prefix, "utf8") <= 155 && Buffer.byteLength(name, "utf8") <= 100)
      return { name, prefix };
  }
  return null;
}

const TYPES = { file: "0", symlink: "2", directory: "5" };

/**
 * Write `entries` to `output`. Each entry is one of
 * { path, directory: true }, { path, symlink }, or { path, executable }, a
 * regular file whose bytes are read from `${root}/${path}`. Paths are relative
 * with `/` separators; a directory's path is written with a trailing `/`.
 */
export function writeArchive(root, entries, output) {
  const fd = openSync(output, "w");
  try {
    for (const entry of entries) {
      const kind = entry.directory ? "directory" : entry.symlink !== undefined ? "symlink" : "file";
      const path = kind === "directory" ? `${entry.path}/` : entry.path;
      const linkname = kind === "symlink" ? entry.symlink : "";
      const content = kind === "file" ? readFileSync(`${root}/${entry.path}`) : Buffer.alloc(0);
      const mode = kind === "directory" ? 0o755 : kind === "symlink" ? 0o777 : entry.executable ? 0o555 : 0o444;
      let split = ustarName(path);
      const records = [];
      if (!split) {
        records.push(paxRecord("path", path));
        // Readers take the PAX path; the ustar field only has to be valid.
        split = { name: "pax-path", prefix: "" };
      }
      if (Buffer.byteLength(linkname, "utf8") > 100) records.push(paxRecord("linkpath", linkname));
      if (records.length) {
        const pax = Buffer.from(records.join(""), "utf8");
        writeSync(fd, header({ name: "PaxHeader", mode: 0o644, size: pax.length, type: "x" }));
        writeSync(fd, pax);
        writeSync(fd, padding(pax.length));
      }
      writeSync(
        fd,
        header({
          name: split.name,
          prefix: split.prefix,
          mode,
          size: content.length,
          type: TYPES[kind],
          linkname: Buffer.byteLength(linkname, "utf8") > 100 ? "" : linkname,
        }),
      );
      if (content.length) {
        writeSync(fd, content);
        writeSync(fd, padding(content.length));
      }
    }
    // The end of an archive is two zero blocks.
    writeSync(fd, Buffer.alloc(BLOCK * 2));
  } finally {
    closeSync(fd);
  }
}
