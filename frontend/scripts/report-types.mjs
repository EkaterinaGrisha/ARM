// Generates src/api/report.generated.ts from the report JSON Schema (docs/report.schema.json).
// Run `npm run gen:types` after the schema changes; `npm test` fails while the file is stale.
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { compile } from "json-schema-to-typescript";

const SCHEMA = new URL("../../docs/report.schema.json", import.meta.url);
export const OUTPUT = new URL("../src/api/report.generated.ts", import.meta.url);

const BANNER = "// Generated from docs/report.schema.json by scripts/report-types.mjs. Do not edit: run `npm run gen:types`.";

/** Adapts the pydantic schema to the report the web receives and to what the generator understands. */
function adapt(node) {
  if (Array.isArray(node)) return node.forEach(adapt);
  if (!node || typeof node !== "object") return;
  // Models accept unknown keys, the web ignores them; an index signature would hide typos in field names.
  if (node.additionalProperties === true) delete node.additionalProperties;
  // The generator reads tuples in the draft-07 form only.
  if (node.prefixItems) {
    node.items = node.prefixItems;
    delete node.prefixItems;
  }
  if (node.properties) {
    const required = new Set(node.required ?? []);
    for (const [key, prop] of Object.entries(node.properties)) {
      // Pydantic titles every field, and the generator would turn each title into a type alias.
      delete prop.title;
      // The API serves reports serialized from the models without null fields,
      // so a field with a non-null default is always present.
      if (prop.default !== undefined && prop.default !== null) required.add(key);
    }
    node.required = [...required];
  }
  Object.values(node).forEach(adapt);
}

export async function renderReportTypes() {
  const schema = JSON.parse(await readFile(SCHEMA, "utf-8"));
  adapt(schema);
  return compile(schema, "Report", {
    bannerComment: BANNER,
    additionalProperties: false,
    style: { bracketSpacing: true, printWidth: 120, trailingComma: "all" },
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await writeFile(OUTPUT, await renderReportTypes());
}
