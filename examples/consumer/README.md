# Independent consumer

Copy this directory outside the ruleset checkout and run:

```sh
python3 run.py --bazel /path/to/bazel
```

Use Bazelisk or the Bazel release in `.bazelversion`. Python is needed only to
orchestrate the demo. Bun and Node are downloaded and verified by Bazel; an
ambient installation is unnecessary. Acquisition needs network access.

`MODULE.bazel` depends on the ruleset as a separate module. Until a versioned
release is available, its `archive_override` selects a fixed public source
snapshot and verifies its integrity. The consumer owns its Node dependency,
Bun archive selection, manifests, lockfile, and lifecycle inputs. It does not
load the ruleset's development extension or depend on its development tools.

`run.py` builds the public `@npm//:files` target and copies only its exported
files, plus the separately declared runtimes, into a temporary directory.
`demo.mjs` checks file digests, restores the closure layout, verifies output
from the Node-invoking lifecycle script, and executes native esbuild under
Node and Bun. Bun auto-install is disabled during execution. To retain the
export instead of running it immediately:

```sh
python3 run.py --export /absolute/path/to/new-directory
/absolute/path/to/new-directory/node /absolute/path/to/new-directory/demo.mjs
```

The restore code illustrates consumption of a trusted acquired closure. It is
not a validator for arbitrary untrusted manifests or an execution sandbox.
The restored directories are writable, and execution is not network-isolated.
See the ruleset README for the acquisition and execution boundaries.

## Updating inputs

For a ruleset update, change the archive URL, integrity, and strip prefix in
`MODULE.bazel` together, and match `bazel_dep` to the archive's module version.
For runtime updates, change the Node toolchain selection or Bun archive pins
in `deps.bzl`, then run the integration checks on the acquisition hosts you
use. These are host-native packages, not cross-platform build outputs.

For npm updates, edit `package.json` and regenerate `bun.lock` using the
selected Bun release. Review both files together. To regenerate without
running lifecycle scripts, use `bun install --lockfile-only --ignore-scripts`.
An ordinary acquisition uses the frozen lock and must not repair disagreement.
Installation source/configuration files must also appear in `bun_install.inputs`.

## Troubleshooting

- An archive integrity failure means the downloaded bytes do not match the
  selected snapshot. Verify the intended source before changing the integrity.
- A frozen-lock error means a manifest and the committed lock disagree.
  Regenerate deliberately using the selected Bun release.
- A missing lifecycle input must be declared in `inputs`; host files and
  ambient tools are not installation inputs.
- A payload digest mismatch or missing native package requires a fresh complete
  export. Do not repair it by installing packages in the restored directory.
- An unsupported acquisition host needs verified runtime pins and real native
  package evidence before it can join the example's platform selection.

## Candidate archive verification

From the ruleset root, run `python3 tests/check_consumer.py`. It tests a source
archive made from committed `HEAD`, copies this consumer outside the checkout,
and uses a temporary local registry with `--ignore_dev_dependency`. Bazel
ignores archive overrides under that flag, so a local registry is necessary to
test an unpublished archive with development dependencies disabled.

The check removes the source archive, copied consumer module, and private Bazel
output directory before executing the export. It also verifies rejection of
corrupt and missing payload bytes. To test a prepared archive instead:

```sh
python3 tests/check_consumer.py --archive /path/to/rules_bun.tar.gz
```

Supply `--strip-prefix` when the archive has a top-level directory. The candidate
must match the module name and version selected by this consumer. This is local
archive validation, not evidence of a published release or BCR acceptance.
