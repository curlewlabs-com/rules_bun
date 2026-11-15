"""The platforms npm units are built for, by Bazel's constraints and npm's names."""

# npm's `os`, `cpu` and `libc` names for each platform, and the config_setting
# in //npm that matches it, by `name`. Linux is glibc because Bun's own build is
# the host libc it installs for, and every Linux Bun but the separate `-musl`
# builds is glibc; macOS has no libc, so Bun admits every package's there.
NPM_PLATFORMS = [
    struct(name = "macos_aarch64", constraints = ("macos", "aarch64"), os = "darwin", cpu = "arm64", libc = None),
    struct(name = "macos_x86_64", constraints = ("macos", "x86_64"), os = "darwin", cpu = "x64", libc = None),
    struct(name = "linux_aarch64", constraints = ("linux", "aarch64"), os = "linux", cpu = "arm64", libc = "glibc"),
    struct(name = "linux_x86_64", constraints = ("linux", "x86_64"), os = "linux", cpu = "x64", libc = "glibc"),
]

def npm_allows(constraint, value):
    """npm's reading of an os, cpu or libc list.

    Args:
      constraint: a package's `os`, `cpu` or `libc` list.
      value: npm's name for the platform's os, cpu or libc.

    Returns:
      Whether the list admits `value`: a `!` entry excludes its value, and any
      other entry limits the package to the values listed.
    """
    if "!" + value in constraint:
        return False
    listed = [entry for entry in constraint if not entry.startswith("!")]
    return not listed or value in listed

def npm_platform_allows(platform, os, cpu, libc):
    """Whether Bun installs a package with these constraints on `platform`.

    Args:
      platform: an NPM_PLATFORMS entry, or a struct with its os, cpu and libc.
      os: the package's `os` list.
      cpu: the package's `cpu` list.
      libc: the package's `libc` list.

    Returns:
      Whether each list admits the platform's value; a platform without a
      libc admits every libc list.
    """
    return (npm_allows(os, platform.os) and npm_allows(cpu, platform.cpu) and
            (platform.libc == None or npm_allows(libc, platform.libc)))
