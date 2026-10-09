# Acquired closure format

`closure.json` describes the workspace acquired by `bun_install`. It travels
with the regular files in the public `:files` target. Its producer is
[bun/install.mjs](../bun/install.mjs); its consumer is the downstream action or
exporter that restores the workspace. This is the format contract, not a claim
that the example reader accepts untrusted manifests safely.

The initial format identifier is `1`. JSON object key order, whitespace,
entry order, and diagnostic text are not part of the contract. SHA-256 values
are lowercase hexadecimal digests of the referenced bytes.

| Field | Meaning |
| --- | --- |
| `format` | Format identifier; reject an unsupported value before restoration. |
| `installer` | SHA-256 of the installer JavaScript used for acquisition. |
| `planner` | SHA-256 of the JavaScript that lists the lock's registry tarballs. |
| `layout` | SHA-256 of the JavaScript that settles the parts of Bun's layout install timing decides. |
| `tools` | Object with `bun` and `node` SHA-256 values for the acquired executables. |
| `inputs` | Object mapping declared workspace-relative input paths to their SHA-256 values. |
| `workspaces` | Selected workspace directories; `.` selects the root dependencies. |
| `entries` | Workspace entries to restore, including installation inputs and lifecycle outputs. |

Every entry has a `path` relative to the workspace root. Paths use `/`, have
no leading slash, and do not contain empty, `.` or `..` components. The root
directory itself has no entry. Entry variants are:

| Shape | Restoration semantics |
| --- | --- |
| `path`, `sha256`, `executable` | Regular file. Read bytes from `workspace/<path>` in the exported payload; see below for when to verify their digest. Restore read-only, retaining executability when the boolean is true. |
| `path`, `directory: true` | Directory. Create it before entries that need it. Directory timestamps and permission bits are not carried. |
| `path`, `symlink` | Symbolic link. The string is a relative target interpreted from the link's parent directory, not from the workspace root. |

Acquisition rejects links whose resolved destination escapes the workspace or
does not exist, and rejects absolute targets. A relative target can contain
`..` when its resolved destination stays inside the workspace. Symlinks are
represented in the manifest rather than exported as regular file labels, so
Bazel need not traverse package-link cycles.

Consumers must preserve the directory/link topology and include every regular
file and the manifest as inputs. Create required directories before restoring
files and links; do not rely on serialized entry order. Acquire a fresh
complete export when payload bytes are missing or corrupted, rather than
allowing Bun to install on demand.

Whether to validate regular-file digests depends on where the export is read.
A consumer that declares the `:files` target as an action input in the same
build need not re-hash it: Bazel's downloader checked each tarball against its
locked integrity, and Bazel tracks the exported files by content, so hashing
them again reads every byte to prove what Bazel already holds. A consumer that
receives the export any other way - copied off its acquisition host, archived,
or read outside Bazel - validates each regular file's digest before execution.

The runtime hashes describe acquisition, not an execution toolchain. Declare
execution runtimes separately. The manifest does not identify every host
library, network response, operating-system property, or external resource a
lifecycle program could read. It therefore does not establish a complete
execution identity or qualify shared action-cache results on its own.

The hashes detect mismatched bytes relative to a trusted manifest; they do not
authenticate an untrusted manifest. Treat an export as executable supply-chain
input. Supply an independent execution sandbox and network policy where those
boundaries are required. See [compatibility](compatibility.md) and
[security reporting](../SECURITY.md).
