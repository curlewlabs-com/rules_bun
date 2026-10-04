# Public API and compatibility

This policy defines the initial release candidate's supported scope. Public
source snapshots are available now; a module version in `MODULE.bazel` does not
by itself mean that a versioned release has been published. Select a reviewed
commit or release archive and verify its integrity.

## Supported configuration

The supported baseline is the Bazel, Bun, and Node combination selected in the
README's [Versions](../README.md#versions) section, on the native Linux and macOS
hosts exercised by [CI](../.github/workflows/ci.yaml). Each release must have
passing evidence for those selections. Other operating-system releases,
architectures, runtime versions, Windows, and cross-installation are outside
that baseline until native package and lifecycle behavior have been verified.
Alternate runtimes used by invalidation tests are test fixtures, not additions
to the supported baseline. Bun and Node execute on the acquisition host; the
consumer owns selection and verification of those binaries and any later
execution platform.

## Public surface

Load `bun_install` from `@curlewlabs_rules_bun//bun:repositories.bzl` using
`use_repo_rule` or a module extension. The rule's attribute documentation in
[repositories.bzl](../bun/repositories.bzl) is the API reference; the
[consumer example](../examples/consumer/README.md) demonstrates the supported
usage. Attribute defaults are defined by that rule, not by this policy.

The acquired repository's public outputs are `:files`, `:closure.json`, and
the regular file labels included in `:files`. The
[closure format](closure-format.md) describes how to restore the payload.
Repository scratch directories, tool symlinks, request files, `files.json`,
installer implementation details, and development extensions are private.
Consumers must not depend on them. Example restoration code illustrates a
trusted closure; it is not a general-purpose untrusted-archive reader.

Installation accepts the original root manifest and text `bun.lock`, together
with explicitly declared text inputs placed at root-relative paths. Binary
installation inputs and the legacy binary `bun.lockb` are outside the API.
Declare every workspace manifest, even when selecting a subset. Workspace
paths must be explicit directories; glob expansion and automatic discovery of
undeclared source/configuration files are not supported. Files needed after
installation through local workspace links remain the consumer's responsibility.

## Lifecycle and registry policy

Bun applies the package manifests' lifecycle and trust policy. The rule does
not disable all lifecycle scripts, add trust declarations, or provide a shell
or native compiler toolchain. Node-invoking scripts receive the declared Node
runtime. The installer verifies that declared input bytes remain unchanged.
A lifecycle script requiring other tools must not assume they are on PATH.

Acquisition is network-enabled and unsandboxed. A private HOME, temporary
directory, and package cache prevent ordinary state reuse; they do not isolate
malicious packages from the host. Only acquire manifests, inputs, and packages
that are trusted to execute on that host. See [SECURITY.md](../SECURITY.md).

Authenticated private registries are not supported by the public API. Ambient
credentials and user environment variables are not forwarded to Bun, and there
is no secret-input or credential-provider attribute. Non-secret Bun registry
configuration may be supplied through declared text inputs. Do not put tokens
or passwords in those inputs: they become part of the exported payload and
may also appear in repository state or diagnostics. There is no credential
redaction guarantee. Supporting authenticated registries requires a separate
design that addresses both acquisition and exported-artifact exposure.

## Failure contract

Invalid input layouts, unsupported workspace selections, frozen-lock
disagreement, missing declared manifests, runtime/installer failures, and
installation timeouts fail repository acquisition. There is no unlocked
fallback install. An installer that modifies a declared
input fails acquisition. Absolute, escaping, or dangling package links, nested Bazel
package boundaries, and unsupported filesystem entries also fail acquisition.
A failed acquisition is not a usable partial closure.

Diagnostic wording comes from Bazel, Bun, Node, and this rule and is not a
stable machine-readable API. Fix the underlying input or host problem and
retry the build. Consumers must reject missing or corrupt exported files
before executing them, and must not repair a closure through auto-install.

## Versioning

Within a pre-stable minor release line, patch releases preserve the public
attributes, output labels, and documented closure semantics. Intentional
incompatible changes require a new minor release, migration notes, and a new
closure format identifier when the serialized contract changes incompatibly.
Additive fields may appear without a format change; consumers should ignore
fields they do not use and reject unsupported format identifiers.

Security fixes may reject previously accepted unsafe input. Release notes
must identify such tightening and any required consumer changes. The latest
published release receives fixes; older release lines have no backport promise.
A future stable release will define its compatibility policy before publication.
