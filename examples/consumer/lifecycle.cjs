const { writeFileSync } = require("node:fs");
const { transformSync } = require("esbuild");

writeFileSync(
  "lifecycle.json",
  JSON.stringify({
    node: process.versions.node,
    output: transformSync("const answer: number = 42;", { loader: "ts" }).code,
  }),
);
