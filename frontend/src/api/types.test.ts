import { expectTypeOf, it } from "vitest";
import type { TitleBlock, TitleField } from "./types";

it("TITLE_FIELDS lists every title block field", () => {
  expectTypeOf<TitleField>().toEqualTypeOf<keyof TitleBlock>();
});
