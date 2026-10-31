import { describe, expect, test } from "bun:test";
import { readLock } from "./npm_lock";

const A = "sha512-AAAA";

function lockWith(packages: unknown, workspaces: unknown = {}): string {
  return JSON.stringify({ lockfileVersion: 1, workspaces, packages });
}

// A field of the wrong shape must fail where it sits, not reach the graph as
// a value the translator trusts.
describe("bun.lock reading", () => {
  test("refuses a lock version it does not read", () => {
    expect(() => readLock(JSON.stringify({ packages: {} }))).toThrow(
      "lockfileVersion undefined is not the version",
    );
  });

  test("names the field of a package entry with the wrong type", () => {
    const cases: [unknown, string][] = [
      ["a@1.0.0", 'packages["a"]: expected a list starting with its spec'],
      [["a@1.0.0", "", [], A], 'packages["a"]: expected an object'],
      [
        ["a@1.0.0", "", { dependencies: { b: 1 } }, A],
        'packages["a"].dependencies.b: expected a string',
      ],
      [
        ["a@1.0.0", "", { optionalPeers: [1] }, A],
        'packages["a"].optionalPeers: expected a string or a list of strings',
      ],
      [
        ["a@1.0.0", "", { os: 3 }, A],
        'packages["a"].os: expected a string or a list of strings',
      ],
      [
        ["a@1.0.0", "", { bin: ["cli.js"] }, A],
        'packages["a"].bin: expected an object',
      ],
      [["a@1.0.0", "", {}, 7], 'packages["a"]: unreadable lock entry'],
    ];
    for (const [entry, message] of cases) {
      const read = () => readLock(lockWith({ a: entry }));
      if (message) expect(read).toThrow(message);
      else expect(read).not.toThrow();
    }
  });

  // `null` is not absence: reading it as an empty section would translate a
  // damaged lock into an empty graph without a word.
  test("refuses null sections rather than reading them as empty", () => {
    for (const section of ["packages", "workspaces"]) {
      expect(() =>
        readLock(JSON.stringify({ lockfileVersion: 1, [section]: null })),
      ).toThrow(`bun.lock ${section}: expected an object`);
    }
  });

  test("names the field of a workspace with the wrong type", () => {
    expect(() =>
      readLock(lockWith({}, { "": { devDependencies: ["prettier"] } })),
    ).toThrow('workspaces[""].devDependencies: expected an object');
  });

  test("normalizes a string bin and a single os to their list forms", () => {
    const entry = readLock(
      lockWith({
        "@s/cli": ["@s/cli@1.0.0", "", { bin: "x.js", os: "linux" }, A],
      }),
    ).packages["@s/cli"];
    expect(entry.kind === "registry" && entry.meta.bin).toEqual({
      cli: "x.js",
    });
    expect(entry.kind === "registry" && entry.meta.os).toEqual(["linux"]);
  });
});
