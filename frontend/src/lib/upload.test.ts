import { describe, expect, it } from "vitest";
import type { Health } from "../api/types";
import { uploadLimitError } from "./upload";

const health: Health = {
  status: "ok",
  version: "0.1.0",
  pipeline: "baseline",
  pipeline_version: "0.1.0",
  ocr: true,
  max_upload_mb: 1,
  max_files: 2,
};

describe("upload limits", () => {
  it("lets an upload within the limits through", () => {
    expect(uploadLimitError(2, 1024 * 1024, health)).toBeNull();
  });

  it("rejects too many files and too many bytes with the server's message", () => {
    expect(uploadLimitError(3, 10, health)).toBe("Файлов в комплекте: 3, допустимо не больше 2. Увеличьте ARM_MAX_FILES");
    expect(uploadLimitError(1, 1024 * 1024 + 1, health)).toBe(
      "Комплект больше 1 МБ: разделите его или увеличьте ARM_MAX_UPLOAD_MB",
    );
  });

  it("does not block when the server does not report limits", () => {
    expect(uploadLimitError(5000, 10 ** 12, null)).toBeNull();
    expect(uploadLimitError(5000, 10 ** 12, { ...health, max_files: undefined, max_upload_mb: undefined })).toBeNull();
  });
});
