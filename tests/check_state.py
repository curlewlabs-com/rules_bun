"""Exercise incremental acquisition and overlapping installs with real runtimes."""

import argparse
import concurrent.futures
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TypedDict


class Lifecycle(TypedDict):
    identity: str
    scratch: list[str]
    bun: str
    node: str


def runtime_sources(bazel: str, root: Path) -> dict[str, Path]:
    os_name = {"Darwin": "darwin", "Linux": "linux"}[platform.system()]
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}[platform.machine()]
    labels = {
        "bun": "@test_bun//:bun",
        "node": f"@nodejs_{os_name}_{arch}//:bin/nodejs/bin/node",
        "previous_bun": "@previous_bun//:bun",
        "previous_node": "@previous_node//:bin/node",
    }
    command = [bazel, "--ignore_all_rc_files"]
    subprocess.run([*command, "build", *labels.values()], cwd=root, check=True)
    execution = Path(
        subprocess.check_output(
            [*command, "info", "execution_root"], cwd=root, text=True
        ).strip()
    )
    return {
        name: execution
        / subprocess.check_output(
            [*command, "cquery", label, "--output=files"], cwd=root, text=True
        ).strip()
        for name, label in labels.items()
    }


def prepare(root: Path, directory: Path) -> None:
    shutil.copytree(root / "tests/state", directory)
    for name in [".bazelversion", "package.json", "bun.lock", "run.py", "demo.mjs"]:
        shutil.copy2(root / "examples/consumer" / name, directory / name)


def state(directory: Path, identity: str, barrier: str = "") -> None:
    (directory / "state.json").write_text(
        json.dumps({"identity": identity, "barrier": barrier})
    )


def export(
    bazel: str,
    output: Path,
    directory: Path,
    destination: Path,
    identity: str,
    tools: Path,
) -> Lifecycle:
    subprocess.run(
        [
            sys.executable,
            str(directory / "run.py"),
            "--bazel",
            bazel,
            "--output-user-root",
            str(output),
            "--export",
            str(destination),
        ],
        check=True,
    )
    lifecycle: Lifecycle = json.loads(
        (destination / "workspace/lifecycle.json").read_text()
    )
    assert lifecycle["identity"] == identity, lifecycle
    expected = {
        runtime: subprocess.check_output([str(tools / runtime), "--version"], text=True)
        .strip()
        .removeprefix("v")
        for runtime in ["bun", "node"]
    }
    assert {"bun": lifecycle["bun"], "node": lifecycle["node"]} == expected, lifecycle
    # Reuse the standalone consumer to check closure digests and native execution.
    subprocess.run(
        [str(destination / "node"), str(destination / "demo.mjs")], check=True
    )
    print(f"Verified acquisition state: {identity}", flush=True)
    return lifecycle


def concurrent_acquisitions(
    bazel: str, output: Path, directories: list[Path], staging: Path, tools: Path
) -> None:
    barrier = threading.Barrier(len(directories))
    arrivals: set[str] = set()
    arrivals_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            with arrivals_lock:
                arrivals.add(self.path)
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                self.send_error(500, "Acquisition peer failed")
                return
            self.send_response(200)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.start()
    try:
        address = f"http://127.0.0.1:{server.server_port}"
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(directories)
        ) as pool:
            futures = []
            try:
                for directory in directories:
                    state(directory, directory.name, address)
                    futures.append(
                        pool.submit(
                            export,
                            bazel,
                            output,
                            directory,
                            staging / f"export-{directory.name}",
                            directory.name,
                            tools,
                        )
                    )
                results = [
                    future.result()
                    for future in concurrent.futures.as_completed(futures)
                ]
            finally:
                # Release a waiting lifecycle if its peer's Bazel process failed.
                barrier.abort()
        assert arrivals == {f"/{directory.name}" for directory in directories}, arrivals
        scratch = [path for result in results for path in result["scratch"]]
        assert len(set(scratch)) == len(scratch), results
        print(
            "Verified overlapping acquisitions with separate HOME, TMPDIR, and cache.",
            flush=True,
        )
    finally:
        barrier.abort()
        server.shutdown()
        server.server_close()
        server_thread.join()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", default="bazel")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sources = runtime_sources(args.bazel, root)
    with tempfile.TemporaryDirectory(prefix="bun-acquisition-state-") as temporary:
        staging = Path(temporary).resolve()
        candidate = staging / "rules_bun"
        candidate.mkdir()
        shutil.copy2(root / "MODULE.bazel", candidate / "MODULE.bazel")
        shutil.copy2(root / "BUILD.bazel", candidate / "BUILD.bazel")
        shutil.copytree(root / "bun", candidate / "bun")
        tools = staging / "tools"
        tools.mkdir()
        (tools / "REPO.bazel").write_text("")
        (tools / "BUILD.bazel").write_text('exports_files(["bun", "node"])\n')
        for runtime in ["bun", "node"]:
            shutil.copy2(sources[runtime], tools / runtime)
        output = staging / "bazel"
        directories = [staging / "left", staging / "right"]
        for directory in directories:
            prepare(root, directory)
        left = directories[0]
        command = [args.bazel, "--ignore_all_rc_files", f"--output_user_root={output}"]
        try:
            state(left, "initial")
            export(args.bazel, output, left, staging / "initial", "initial", tools)

            # Changing a declared lifecycle input must replace old output without a clean.
            state(left, "changed-input")
            export(
                args.bazel,
                output,
                left,
                staging / "changed-input",
                "changed-input",
                tools,
            )

            # Only script bytes change: Starlark and repository attributes stay fixed.
            for script in ["install", "plan"]:
                path = candidate / f"bun/{script}.mjs"
                original = path.read_text()
                path.write_text(
                    f'throw new Error("Updated {script} script executed");\n' + original
                )
                result = subprocess.run(
                    [*command, "build", "@npm//:files"],
                    cwd=left,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                assert (
                    result.returncode != 0
                    and f"Updated {script} script executed" in result.stderr
                ), result.stdout + result.stderr
                path.write_text(original)
                export(
                    args.bazel,
                    output,
                    left,
                    staging / f"restored-{script}",
                    "changed-input",
                    tools,
                )
                print(
                    f"Verified {script} script invalidation and recovery without a clean.",
                    flush=True,
                )

            # Replace real executable bytes at the same label and path, one at a time.
            for runtime in ["bun", "node"]:
                target = tools / runtime
                target.unlink()
                shutil.copy2(sources[f"previous_{runtime}"], target)
                export(
                    args.bazel,
                    output,
                    left,
                    staging / f"changed-{runtime}",
                    "changed-input",
                    tools,
                )
                print(f"Verified {runtime} replacement without a clean.", flush=True)

            concurrent_acquisitions(args.bazel, output, directories, staging, tools)
        finally:
            for directory in directories:
                subprocess.run([*command, "shutdown"], cwd=directory, check=True)


if __name__ == "__main__":
    main()
