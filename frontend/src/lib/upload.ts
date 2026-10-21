import type { Health } from "../api/types";

const MB = 1024 * 1024;

/**
 * The server's upload limits, checked before sending: when the server rejects a large upload early,
 * the browser may see only a dropped connection instead of the message. Texts match backend/arm_web/main.py.
 */
export function uploadLimitError(count: number, bytes: number, health: Health | null): string | null {
  if (health?.max_files != null && count > health.max_files) {
    return `Файлов в комплекте: ${count}, допустимо не больше ${health.max_files}. Увеличьте ARM_MAX_FILES`;
  }
  if (health?.max_upload_mb != null && bytes > health.max_upload_mb * MB) {
    return `Комплект больше ${health.max_upload_mb} МБ: разделите его или увеличьте ARM_MAX_UPLOAD_MB`;
  }
  return null;
}
