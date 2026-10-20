import { readFile } from "node:fs/promises";
import { expect, it } from "vitest";
import { OUTPUT, renderReportTypes } from "./report-types.mjs";

it("src/api/report.generated.ts matches docs/report.schema.json", async () => {
  const committed = await readFile(OUTPUT, "utf-8");
  expect(committed, "stale generated types: run npm run gen:types").toBe(await renderReportTypes());
});
