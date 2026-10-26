# Acquired closure format

`closure.json` describes the workspace acquired by `bun_install`. It travels
with the regular files in the public `:files` target. Its producer is
[bun/install.mjs](../bun/install.mjs); its consumer is the downstream action or
exporter that restores the workspace. This is the format contract, not a claim
that the example reader accepts untrusted manifests safely.

The current format identifier is `2`; [format history](#format-history) says
what each one changed. JSON object key order, whitespace, entry order, and
diagnostic text are not part of the contract. SHA-256 values are lowercase
hexadecimal digests of the referenced bytes.

| Field | Meaning |
| --- | --- |
| `format` | Format identifier; reject an unsupported value before restoration. |
| `installer` | SHA-256 of the installer JavaScript used for acquisition. |
| `planner` | SHA-256 of the JavaScript that lists the lock's registry tarballs. |
| `layout` | SHA-256 of the JavaScript that settles the parts of Bun's layout install timing decides. |
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

The manifest does not identify every host library, network response,
operating-system property, or external resource a lifecycle program could read.
It therefore does not establish a complete execution identity or qualify shared
action-cache results on its own.

The hashes detect mismatched bytes relative to a trusted manifest; they do not
authenticate an untrusted manifest. Treat an export as executable supply-chain
input. Supply an independent execution sandbox and network policy where those
boundaries are required. See [compatibility](compatibility.md) and
[security reporting](../SECURITY.md).

## Closure archive

`closure.tar`, exposed as `:archive`, holds the same tree as one file: every
entry in `entries`, at the same workspace-relative path, without the
`workspace/` prefix the `:files` labels carry. Declaring it makes the closure
one action input, where `:files` makes it one input per regular file.

The archive is reproducible: the same entries give the same bytes on every
host, so its digest is a stable action input. Entries appear in the manifest's
sorted walk order, with modification times of zero, owner and group zero and
empty, and modes `0755` for directories, `0777` for symlinks, and `0555` or
`0444` for regular files by their executable bit. It is an uncompressed
POSIX.1-2001 (pax) tar: paths and link targets longer than the ustar fields go
in an extended header. It is uncompressed because compressed bytes can differ
with the compressor's build.

`closure.json` remains the description of the layout; a consumer that expands
the archive can check what it expanded against `entries`. The archive adds no
manifest field, so the format identifier is unchanged.

## Acquisition runtimes

`tools.json`, exported beside `closure.json` but outside `:files`, is an object
with `bun` and `node` SHA-256 values for the executables that ran acquisition.
Those are the acquisition host's own binaries, so hosts on different platforms
that acquire the same closure record different values. Kept out of `:files`,
they do not reach the inputs of an action that reads the closure, and the
action's cache key does not depend on which host acquired it. Declare
`:tools.json` only where the acquiring runtimes themselves matter.

The runtime hashes describe acquisition, not an execution toolchain. Declare
execution runtimes separately.

## Format history

| Format | Change |
| --- | --- |
| `1` | Initial format. `closure.json` also carried the runtime digests, as a `tools` field holding what `tools.json` now holds. |
| `2` | The runtime digests moved to `tools.json`; every other field is unchanged. A reader that does not use them can accept both. |
