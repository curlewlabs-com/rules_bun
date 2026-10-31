"""The platforms npm units are built for, by Bazel's constraints and npm's names."""

# npm's `os` and `cpu` names for each platform, and the config_setting in
# //npm that matches it, by `name`.
NPM_PLATFORMS = [
    struct(name = "macos_aarch64", constraints = ("macos", "aarch64"), os = "darwin", cpu = "arm64"),
    struct(name = "macos_x86_64", constraints = ("macos", "x86_64"), os = "darwin", cpu = "x64"),
    struct(name = "linux_aarch64", constraints = ("linux", "aarch64"), os = "linux", cpu = "arm64"),
    struct(name = "linux_x86_64", constraints = ("linux", "x86_64"), os = "linux", cpu = "x64"),
]

def npm_allows(constraint, value):
    """npm's reading of an os or cpu list.

    Args:
      constraint: a package's `os` or `cpu` list.
      value: npm's name for the platform's os or cpu.

    Returns:
      Whether the list admits `value`: a `!` entry excludes its value, and any
      other entry limits the package to the values listed.
    """
    if "!" + value in constraint:
        return False
    listed = [entry for entry in constraint if not entry.startswith("!")]
    return not listed or value in listed
