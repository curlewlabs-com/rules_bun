import { describe, expect, test } from "bun:test";
import { canonical, hoistPattern, render, translate } from "./npm_graph";

const A = "sha512-AAAA";
const B = "sha512-BBBB";

function lock(
  dependencies: Record<string, string>,
  packages: Record<string, unknown[]>,
): string {
  return JSON.stringify({
    lockfileVersion: 1,
    workspaces: { "": { name: "root", dependencies } },
    packages,
  });
}

function only(names: string[]): Record<string, string> {
  return Object.fromEntries(names.map((name) => [name, "1"]));
}

describe("npm lock translation", () => {
  // Bun writes trailing commas, and JSONC allows comments; json.decode reads
  // neither, which is why translation runs under Bun at all.
  test("reads bun.lock's JSONC", () => {
    const graph = translate(`{
      // a comment
      "lockfileVersion": 1,
      "workspaces": { "": { "name": "root", "dependencies": { "a": "1" } } },
      "packages": {
        "a": ["a@1.0.0", "", {}, "${A}"],
      },
    }`);
    expect(graph.workspaces["."]).toEqual({ a: "a@1.0.0" });
  });

  // A nested copy must win over the hoisted one, or a consumer links the
  // version its parent did not ask for.
  test("resolves each dependency from the package's own install path outward", () => {
    const graph = translate(
      lock(only(["@s/p", "q"]), {
        "@s/p": ["@s/p@1.0.0", "", { dependencies: only(["q", "r"]) }, A],
        "@s/p/q": ["q@2.0.0", "", { dependencies: only(["r"]) }, B],
        q: ["q@1.0.0", "", {}, A],
        r: ["r@1.0.0", "", {}, B],
      }),
    );
    expect(graph.packages["@s/p@1.0.0"].edges).toEqual({
      q: "q@2.0.0",
      r: "r@1.0.0",
    });
    expect(graph.packages["q@2.0.0"].edges).toEqual({ r: "r@1.0.0" });
  });

  // Bun links a peer from the package's dependents, so the same release
  // links a different peer under each; a single node would hand one of them
  // the wrong version (observed: @typescript/vfs's typescript under
  // @bufbuild/protoplugin).
  test("gives a release one node per peer resolution its dependents give it", () => {
    const graph = translate(
      lock(only(["a", "b", "t"]), {
        a: ["a@1.0.0", "", { dependencies: only(["v", "t"]) }, A],
        "a/t": ["t@1.0.0", "", {}, B],
        b: ["b@1.0.0", "", { dependencies: only(["v"]) }, A],
        t: ["t@2.0.0", "", {}, A],
        v: ["v@1.0.0", "", { peerDependencies: only(["t"]) }, B],
      }),
    );
    const underA = graph.packages["a@1.0.0"].edges.v;
    const underB = graph.packages[graph.workspaces["."].b].edges.v;
    expect(underA).not.toBe(underB);
    expect(graph.packages[underA].edges).toEqual({ t: "t@1.0.0" });
    expect(graph.packages[underB].edges).toEqual({ t: "t@2.0.0" });
    // The variants share the release's one download.
    expect(graph.packages[underA].repository).toBe(
      graph.packages[underB].repository,
    );
  });

  // A package that requires what it does not declare finds it in Bun's
  // hoisted fallback (observed: @cloudflare/vitest-pool-workers requires
  // workerd), so the graph names the copy Bun hoists: the top-level one, and
  // of a release installed under several peer variants, the variant whose
  // peers are the hoisted copies.
  test("hoists each allowed top-level release, choosing among peer variants by the hoisted peers", () => {
    const graph = translate(
      lock(only(["a", "b", "t"]), {
        a: ["a@1.0.0", "", { dependencies: only(["v", "t"]) }, A],
        "a/t": ["t@1.0.0", "", {}, B],
        b: ["b@1.0.0", "", { dependencies: only(["v"]) }, A],
        t: ["t@2.0.0", "", {}, A],
        v: ["v@1.0.0", "", { peerDependencies: only(["t"]) }, B],
      }),
      ["t", "v"],
    );
    expect(graph.hoisted.t).toBe("t@2.0.0");
    // v has a variant linking t@1.0.0 and one linking t@2.0.0; the hoisted
    // t is t@2.0.0.
    expect(graph.packages[graph.hoisted.v].edges).toEqual({ t: "t@2.0.0" });
    // Bun hoists nothing hoistPattern leaves out, so a tree that links more
    // would hide an undeclared require that fails under Bun.
    expect(Object.keys(graph.hoisted).sort()).toEqual(["t", "v"]);
  });

  // An allowed name with nothing installed is an entry the lock outgrew; Bun
  // would hoist nothing, so generation names it rather than drop it silently.
  test("fails on an allowed name no workspace installs at the top level", () => {
    const packages = {
      a: ["a@1.0.0", "", { dependencies: only(["n"]) }, A],
      "a/n": ["n@1.0.0", "", {}, B],
      z: ["z@1.0.0", "", {}, A],
    };
    expect(() => translate(lock(only(["a"]), packages), ["n"])).toThrow(
      "hoistPattern names n",
    );
    expect(() => translate(lock(only(["a"]), packages), ["z"])).toThrow(
      "hoistPattern names z",
    );
  });

  // Bun hoists what it installs: a release installed under one variant
  // hoists that variant even when its top-level path alone would bind its
  // peer differently (observed: @typescript/vfs under @bufbuild/protoplugin),
  // and a top-level release no workspace reaches is not installed at all.
  test("hoists a sole installed variant, and installs no unreached release", () => {
    const graph = translate(
      lock(only(["a"]), {
        a: ["a@1.0.0", "", { dependencies: only(["v", "t"]) }, A],
        "a/t": ["t@1.0.0", "", {}, B],
        t: ["t@2.0.0", "", {}, A],
        v: ["v@1.0.0", "", { peerDependencies: only(["t"]) }, B],
        z: ["z@1.0.0", "", {}, A],
      }),
      ["v"],
    );
    expect(graph.packages[graph.hoisted.v].edges).toEqual({ t: "t@1.0.0" });
    expect(graph.packages["z@1.0.0"]).toBeUndefined();
  });

  // Bun reads each entry as a glob, and without the key hoists every name; in
  // either case the graph's literal names would not be what Bun links.
  test("reads hoistPattern as literal package names only", () => {
    expect(
      hoistPattern('[install]\nhoistPattern = ["workerd", "@s/p"]\n'),
    ).toEqual(["workerd", "@s/p"]);
    expect(() =>
      hoistPattern('[install]\nhoistPattern = ["*eslint*"]\n'),
    ).toThrow("is not a literal package name");
    expect(() => hoistPattern('[install]\nhoistPattern = ["!a"]\n')).toThrow(
      "is not a literal package name",
    );
    expect(() => hoistPattern("[install]\n")).toThrow(
      "without it Bun hoists every name",
    );
  });

  // Bun links an optional peer whenever one is found, from a dependent or
  // from the package's own lookup (observed: fdir's picomatch).
  test("links an optional peer when one is found and omits it otherwise", () => {
    const peers = {
      peerDependencies: only(["found", "lookup", "absent"]),
      optionalPeers: ["found", "lookup", "absent"],
    };
    const graph = translate(
      lock(only(["host"]), {
        host: [
          "host@1.0.0",
          "",
          { dependencies: only(["plugin", "found"]) },
          A,
        ],
        found: ["found@1.0.0", "", {}, B],
        lookup: ["lookup@1.0.0", "", {}, A],
        plugin: ["plugin@1.0.0", "", peers, B],
      }),
    );
    const host = graph.packages[graph.workspaces["."].host];
    expect(graph.packages[host.edges.plugin].edges).toEqual({
      found: "found@1.0.0",
      lookup: "lookup@1.0.0",
    });
  });

  test("fails on a required dependency or peer the lock does not hold", () => {
    expect(() =>
      translate(
        lock(only(["a"]), {
          a: ["a@1.0.0", "", { dependencies: only(["gone"]) }, A],
        }),
      ),
    ).toThrow("a: dependency gone resolves to no locked package");
    expect(() =>
      translate(
        lock(only(["a"]), {
          a: ["a@1.0.0", "", { peerDependencies: only(["gone"]) }, A],
        }),
      ),
    ).toThrow("a: peer gone resolves to no locked package");
  });

  // Bun's store keeps one directory per release and peer set, wherever the
  // lock installs it.
  test("makes a release installed at several paths one node", () => {
    const graph = translate(
      lock(only(["x", "z"]), {
        x: ["x@1.0.0", "", { dependencies: only(["y"]) }, A],
        "x/y": ["y@1.0.0", "", {}, B],
        z: ["z@1.0.0", "", { dependencies: only(["y"]) }, A],
        "z/y": ["y@1.0.0", "", {}, B],
      }),
    );
    expect(Object.keys(graph.packages)).toEqual([
      "x@1.0.0",
      "y@1.0.0",
      "z@1.0.0",
    ]);
    expect(Object.keys(graph.repositories)).toEqual([
      "npm__x__1.0.0",
      "npm__y__1.0.0",
      "npm__z__1.0.0",
    ]);
  });

  // A release-keyed node would otherwise silently take whichever install
  // path's edges were walked first.
  test("fails when install paths of one release disagree on its edges", () => {
    expect(() =>
      translate(
        lock(only(["x", "w", "z"]), {
          x: ["x@1.0.0", "", { dependencies: only(["y"]) }, A],
          "x/y": ["y@1.0.0", "", { dependencies: only(["z"]) }, B],
          "x/y/z": ["z@1.0.0", "", {}, A],
          w: ["w@1.0.0", "", { dependencies: only(["y"]) }, A],
          "w/y": ["y@1.0.0", "", { dependencies: only(["z"]) }, B],
          z: ["z@2.0.0", "", {}, B],
        }),
      ),
    ).toThrow("install y@1.0.0 with different edges");
  });

  // With no ancestor providing a peer, each install path's own lookup picks
  // it, so install paths of one release can link different peers, and each
  // such answer is its own node rather than an id with conflicting edges.
  test("keys a node by the peer its own lookup falls back to", () => {
    const graph = translate(
      lock(only(["a", "b"]), {
        a: ["a@1.0.0", "", { dependencies: only(["v"]) }, A],
        "a/v": ["v@1.0.0", "", { peerDependencies: only(["t"]) }, B],
        "a/t": ["t@1.0.0", "", {}, A],
        b: ["b@1.0.0", "", { dependencies: only(["v"]) }, B],
        "b/v": ["v@1.0.0", "", { peerDependencies: only(["t"]) }, B],
        t: ["t@2.0.0", "", {}, B],
      }),
    );
    const underA = graph.packages[graph.workspaces["."].a].edges.v;
    const underB = graph.packages[graph.workspaces["."].b].edges.v;
    expect(underA).not.toBe(underB);
    expect(graph.packages[underA].edges).toEqual({ t: "t@1.0.0" });
    expect(graph.packages[underB].edges).toEqual({ t: "t@2.0.0" });
  });

  // An identity key that splits identical trees costs a unit and a link
  // entry per extra node, so how a peer was found must not split a node when
  // the peer it found is the same.
  test("merges an ancestor-provided peer and a fallback to the same node", () => {
    const graph = translate(
      lock(only(["a", "b"]), {
        a: ["a@1.0.0", "", { dependencies: only(["v", "t"]) }, A],
        b: ["b@1.0.0", "", { dependencies: only(["v"]) }, B],
        t: ["t@1.0.0", "", {}, A],
        v: ["v@1.0.0", "", { peerDependencies: only(["t"]) }, B],
      }),
    );
    const underA = graph.packages[graph.workspaces["."].a].edges.v;
    const underB = graph.packages[graph.workspaces["."].b].edges.v;
    expect(underA).toBe(underB);
    expect(graph.packages[underA].edges).toEqual({ t: "t@1.0.0" });
  });

  test("merges fallbacks from different install paths to the same node", () => {
    const graph = translate(
      lock(only(["a", "b"]), {
        a: ["a@1.0.0", "", { dependencies: only(["v"]) }, A],
        "a/v": ["v@1.0.0", "", { peerDependencies: only(["t"]) }, B],
        "a/t": ["t@1.0.0", "", {}, A],
        b: ["b@1.0.0", "", { dependencies: only(["v"]) }, B],
        "b/v": ["v@1.0.0", "", { peerDependencies: only(["t"]) }, B],
        "b/t": ["t@1.0.0", "", {}, A],
      }),
    );
    const underA = graph.packages[graph.workspaces["."].a].edges.v;
    const underB = graph.packages[graph.workspaces["."].b].edges.v;
    expect(underA).toBe(underB);
    expect(
      Object.keys(graph.packages).filter((id) => id.startsWith("v@")),
    ).toEqual([underA]);
  });

  // An id that a pass count over the whole graph decides would rename every
  // peer variant whenever any part of the lock needed one more pass, and the
  // epic's point is that a bump rebuilds only what reaches it. Here the added
  // part is unconnected, holds peer variants that differ only at their deepest
  // dependency, and shifts the class numbering ahead of the cycle.
  test("keeps every existing id when an unconnected deeper part is added", () => {
    const base = {
      a: ["a@1.0.0", "", { dependencies: only(["v", "t"]) }, A],
      "a/t": ["t@1.0.0", "", {}, B],
      b: ["b@1.0.0", "", { dependencies: only(["v", "m"]) }, A],
      t: ["t@2.0.0", "", {}, A],
      v: ["v@1.0.0", "", { peerDependencies: only(["t"]) }, B],
      m: ["m@1.0.0", "", { dependencies: only(["n"]) }, B],
      n: ["n@1.0.0", "", { peerDependencies: only(["m", "t"]) }, A],
    };
    const deep = {
      x: ["x@1.0.0", "", { dependencies: only(["c1", "s"]) }, A],
      "x/s": ["s@1.0.0", "", {}, B],
      y: ["y@1.0.0", "", { dependencies: only(["c1", "s"]) }, B],
      s: ["s@2.0.0", "", {}, A],
      c1: ["c1@1.0.0", "", { dependencies: only(["c2"]) }, A],
      c2: ["c2@1.0.0", "", { dependencies: only(["c3"]) }, B],
      c3: ["c3@1.0.0", "", { peerDependencies: only(["s"]) }, A],
    };
    const before = translate(lock(only(["a", "b"]), base));
    const after = translate(
      lock(only(["a", "b", "x", "y"]), { ...deep, ...base }),
    );
    expect(
      Object.keys(before.packages).filter((id) => id.includes("+")),
    ).not.toEqual([]);
    for (const [id, node] of Object.entries(before.packages)) {
      expect(after.packages[id]).toEqual(node);
    }
    const c1 = Object.keys(after.packages).filter((id) => id.startsWith("c1@"));
    expect(c1.length).toBe(2);
  });

  // A cycle's members compared by a numbering that means something only
  // inside a cycle let different cycles produce the same key, so a P
  // node silently replaced the other. Here each P binds its peer t by a
  // different route, so the build keeps them apart, and each sits in a cycle
  // with a different release of d at the same place.
  test("keeps cycles apart whose members differ only in the cycle partner", () => {
    const graph = translate(
      lock(only(["x", "y"]), {
        x: ["x@1.0.0", "", { dependencies: only(["p", "t"]) }, A],
        "x/p": [
          "p@1.0.0",
          "",
          { dependencies: only(["d"]), peerDependencies: only(["t"]) },
          B,
        ],
        "x/p/d": ["d@1.0.0", "", { peerDependencies: only(["p"]) }, A],
        y: ["y@1.0.0", "", { dependencies: only(["p"]) }, B],
        "y/p": [
          "p@1.0.0",
          "",
          { dependencies: only(["d"]), peerDependencies: only(["t"]) },
          B,
        ],
        "y/p/d": ["d@2.0.0", "", { peerDependencies: only(["p"]) }, B],
        t: ["t@1.0.0", "", {}, A],
      }),
    );
    const px = graph.packages[graph.workspaces["."].x].edges.p;
    const py = graph.packages[graph.workspaces["."].y].edges.p;
    expect(px).not.toBe(py);
    expect(graph.packages[px].edges.d.startsWith("d@1.0.0")).toBe(true);
    expect(graph.packages[py].edges.d.startsWith("d@2.0.0")).toBe(true);
  });

  // Equality must be decided over the whole graph: the same structure seen
  // from inside a cycle and from outside it is one node.
  test("merges a node in a cycle with its duplicate outside the cycle", () => {
    const repositories = {
      rm: { name: "m", version: "1.0.0", url: "", integrity: A },
      rn: { name: "n", version: "1.0.0", url: "", integrity: B },
    };
    const final = canonical(
      {
        "m@1.0.0+in": { repository: "rm", edges: { n: "n@1.0.0+in" } },
        "n@1.0.0+in": { repository: "rn", edges: { m: "m@1.0.0+in" } },
        "m@1.0.0+out": { repository: "rm", edges: { n: "n@1.0.0+in" } },
      },
      repositories,
    );
    expect(final.get("m@1.0.0+out")).toBe(final.get("m@1.0.0+in"));
    expect(final.get("n@1.0.0+in")).not.toBe(final.get("m@1.0.0+in"));
  });

  // Reaching a node while it is still being built used to return its id
  // unchecked, so an install path with different edges merged silently.
  test("compares an install path that reaches a node still being built", () => {
    expect(() =>
      translate(
        lock(only(["y"]), {
          y: ["y@1.0.0", "", { dependencies: only(["z"]) }, A],
          "y/z": ["z@1.0.0", "", { dependencies: only(["y"]) }, B],
          "y/z/y": ["y@1.0.0", "", { dependencies: only(["q"]) }, A],
          "y/z/y/q": ["q@1.0.0", "", {}, B],
        }),
      ),
    ).toThrow("y and y/z/y install y@1.0.0 with different edges");
  });

  // The URL is the registry's own tarball path; a scoped package's file
  // drops its scope.
  test("names the registry tarball and its integrity", () => {
    const graph = translate(
      lock(only(["@s/p"]), { "@s/p": ["@s/p@1.2.3-rc.1", "", {}, A] }),
    );
    expect(graph.repositories["npm__s__p__1.2.3-rc.1"]).toEqual({
      name: "@s/p",
      version: "1.2.3-rc.1",
      url: "https://registry.npmjs.org/@s/p/-/p-1.2.3-rc.1.tgz",
      integrity: A,
    });
  });

  // Bazel targets cannot form a cycle, so only the members are marked; a
  // package that merely reaches the cycle is not.
  test("marks every member of a dependency cycle with one group", () => {
    const graph = translate(
      lock(only(["entry"]), {
        entry: ["entry@1.0.0", "", { dependencies: only(["m"]) }, A],
        m: ["m@1.0.0", "", { dependencies: only(["n"]) }, B],
        n: ["n@1.0.0", "", { peerDependencies: only(["m"]) }, A],
      }),
    );
    const n = graph.packages["m@1.0.0"].edges.n;
    expect(graph.packages[n].edges).toEqual({ m: "m@1.0.0" });
    expect(graph.packages["m@1.0.0"].cycle).toBe("m@1.0.0");
    expect(graph.packages[n].cycle).toBe("m@1.0.0");
    expect(graph.packages["entry@1.0.0"].cycle).toBeUndefined();
  });

  test("names other workspaces and refuses packages from another source", () => {
    expect(
      translate(
        lock(only(["w", "a"]), {
          w: ["w@workspace:pkg/w"],
          a: ["a@1.0.0", "", {}, A],
        }),
      ).workspaces["."],
    ).toEqual({ a: "a@1.0.0", w: "workspace:pkg/w" });
    expect(() =>
      translate(
        lock(only(["g"]), { g: ["g@github:o/g#abc", "o-g-abc", {}, A] }),
      ),
    ).toThrow("only default-registry packages are translated");
  });

  // Bun keys a workspace's own copy of a dependency by the workspace's
  // package name, not its directory; resolving from the directory falls
  // through to the root's copy, and the workspace links the version it did
  // not ask for.
  test("resolves a workspace's dependency from the workspace's own copy first", () => {
    const graph = translate(
      JSON.stringify({
        lockfileVersion: 1,
        workspaces: {
          "": { name: "root", dependencies: only(["a"]) },
          "pkg/w": { name: "@s/w", dependencies: only(["a", "b"]) },
        },
        packages: {
          "@s/w": ["@s/w@workspace:pkg/w"],
          a: ["a@1.0.0", "", {}, A],
          "@s/w/a": ["a@2.0.0", "", { dependencies: only(["b"]) }, B],
          b: ["b@1.0.0", "", {}, A],
        },
      }),
    );
    expect(graph.workspaces["."]).toEqual({ a: "a@1.0.0" });
    expect(graph.workspaces["pkg/w"]).toEqual({ a: "a@2.0.0", b: "b@1.0.0" });
    expect(graph.packages["a@2.0.0"].edges).toEqual({ b: "b@1.0.0" });
  });

  // The rendered file is what the extension's json.decode reads.
  test("renders strict JSON that round-trips", () => {
    const graph = translate(
      lock(only(["a"]), { a: ["a@1.0.0", "", { bin: { a: "cli.js" } }, A] }),
    );
    expect(JSON.parse(render(graph))).toEqual(graph);
  });
});
