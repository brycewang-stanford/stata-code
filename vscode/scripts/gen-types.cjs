// Regenerate TypeScript types from ../schema/run_result.schema.json.
//
// The hand-rolled types in src/types/runResult.ts are a minimal subset
// kept in sync manually. Run `npm run gen-types` to refresh a *full*
// generated copy at src/types/runResult.generated.ts so you can compare
// the two and pull in any newly-added fields.

const path = require("node:path");
const fs = require("node:fs");
const { compile } = require("json-schema-to-typescript");

const repoRoot = path.resolve(__dirname, "..", "..");
const schemaPath = path.join(repoRoot, "schema", "run_result.schema.json");
const outPath = path.join(__dirname, "..", "src", "types", "runResult.generated.ts");

async function main() {
  const schema = JSON.parse(fs.readFileSync(schemaPath, "utf-8"));
  const ts = await compile(schema, "RunResult", {
    bannerComment: [
      "/**",
      " * AUTO-GENERATED from ../../../schema/run_result.schema.json.",
      " * Do not edit by hand — re-run `npm run gen-types` instead.",
      " */",
      "",
    ].join("\n"),
    style: { bracketSpacing: true, singleQuote: false },
  });
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, ts);
  console.log(`wrote: ${outPath}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
