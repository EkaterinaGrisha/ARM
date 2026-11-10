import type {
  DiscrepancyType,
  DocumentKind,
  DocumentOrigin,
  DocumentStatus,
  ExtractionMethod,
  JobStatus,
  NodeKind,
  NodeStatus,
  ParameterCategory,
  Severity,
  TitleField,
} from "../api/types";

export const DISCREPANCY_TYPES: Record<DiscrepancyType, { label: string; hint: string }> = {
  missing_drawing: { label: "Чертёж отсутствует", hint: "Позиция есть в спецификации, а чертежа в комплекте нет" },
  orphan_drawing: { label: "Чертёж вне спецификаций", hint: "Чертёж есть, но не входит ни в одну спецификацию" },
  name_mismatch: { label: "Расхождение наименования", hint: "Наименование на чертеже отличается от спецификации" },
  duplicate_designation: { label: "Дублирование обозначения", hint: "Одно обозначение у нескольких документов или позиций" },
  revision_mismatch: { label: "Литера / номер изменения", hint: "Разные версии документа или несогласованные литеры" },
  missing_specification: { label: "Нет спецификации СЕ", hint: "Сборочная единица указана, но её спецификации нет" },
  sheets_missing: { label: "Не хватает листов", hint: "В комплекте меньше листов документа, чем в основной надписи" },
  format_mismatch: { label: "Расхождение формата", hint: "Формат в спецификации не совпадает с форматом листов документа" },
  section_mismatch: {
    label: "Позиция не в своём разделе",
    hint: "Сборочная единица записана в «Деталях» или деталь — в «Сборочных единицах»",
  },
  position_anomaly: { label: "Нарушение позиций", hint: "Номер позиции повторяется или у позиции нет количества" },
  first_use_mismatch: {
    label: "Первичное применение",
    hint: "«Перв. примен.» называет сборочную единицу комплекта, в спецификации которой документа нет",
  },
};
export const PRELIMINARY_HINT =
  "В комплекте есть документы, которые не прочитаны: искомый может быть среди них — они перечислены в сообщении";

export const DISCREPANCY_ORDER: DiscrepancyType[] = [
  "missing_drawing",
  "missing_specification",
  "orphan_drawing",
  "sheets_missing",
  "duplicate_designation",
  "revision_mismatch",
  "name_mismatch",
  "section_mismatch",
  "position_anomaly",
  "format_mismatch",
  "first_use_mismatch",
];

export const SEVERITY: Record<Severity, string> = { high: "Высокая", medium: "Средняя", low: "Низкая" };

export const DOCUMENT_STATUS: Record<DocumentStatus, { label: string; hint: string }> = {
  ok: { label: "Обработан", hint: "Документ прочитан и разобран" },
  warning: { label: "С замечаниями", hint: "Обработан частично — проверьте пояснение" },
  damaged: { label: "Повреждён", hint: "Файл не открывается" },
  unreadable: { label: "Нечитаемый скан", hint: "Качество изображения не позволяет распознать текст" },
  blank: { label: "Пустой лист", hint: "На листах файла нет ни текста, ни линий — только бумага" },
  foreign: { label: "Посторонний", hint: "Не является чертежом или спецификацией" },
  unsupported: { label: "Не поддерживается", hint: "Формат файла не поддерживается или изображение больше лимита" },
  encrypted: { label: "Защищён паролем", hint: "PDF открывается только с паролем" },
  duplicate_file: { label: "Копия файла", hint: "Побайтовая копия другого файла комплекта, повторно не разбирается" },
};
export const PROBLEM_STATUSES: DocumentStatus[] = [
  "damaged",
  "encrypted",
  "unreadable",
  "blank",
  "foreign",
  "unsupported",
  "duplicate_file",
  "warning",
];

export const DOCUMENT_KIND: Record<DocumentKind, string> = {
  specification: "Спецификация",
  assembly_drawing: "Сборочный чертёж",
  part_drawing: "Чертёж детали",
  drawing: "Чертёж",
  other: "Прочий документ",
  unknown: "Не определён",
};

export const ORIGIN: Record<DocumentOrigin, string> = {
  vector: "Векторный",
  scan: "Скан",
  mixed: "Смешанный",
  unknown: "—",
};

export const NODE_KIND: Record<NodeKind, string> = {
  kit: "Комплект",
  assembly: "Сборочная единица",
  part: "Деталь",
  standard: "Стандартное изделие",
  other_item: "Прочее изделие",
  material: "Материал",
  document: "Документ",
  unknown: "—",
};

export const NODE_STATUS: Record<NodeStatus, string> = {
  ok: "Документ есть",
  missing: "Нет документа",
  warning: "Есть замечания",
  not_required: "Не требуется",
};

export const PARAMETER_CATEGORY: Record<ParameterCategory, string> = {
  dimension_tolerance: "Допуск размера",
  general_tolerance: "Общие допуски",
  roughness: "Шероховатость",
  geometric: "Форма и расположение",
  heat_treatment: "Термообработка",
  hardness: "Твёрдость",
  coating: "Покрытие",
  other: "Прочее",
};

export const METHOD: Record<ExtractionMethod, string> = {
  text_layer: "текстовый слой PDF",
  ocr: "OCR",
  vlm: "VLM",
  llm: "LLM",
  filename: "имя файла",
  rule: "правило",
  manual: "вручную",
};

export const TITLE_FIELD_LABELS: Record<TitleField, string> = {
  designation: "Обозначение",
  name: "Наименование",
  material: "Материал",
  mass: "Масса",
  scale: "Масштаб",
  litera: "Литера",
  revision: "Изм.",
  archive_number: "Арх. №",
  first_use: "Перв. примен.",
  sheet: "Лист",
  sheets: "Листов",
  format: "Формат",
};

export const JOB_STATUS: Record<JobStatus, string> = {
  queued: "В очереди",
  running: "Обработка",
  done: "Готово",
  failed: "Ошибка",
};

export const CRITICAL_RULES: Record<string, { label: string; hint: string }> = {
  tight_fit: { label: "Точный квалитет", hint: "IT6 и точнее: шлифование, контроль калибрами" },
  fine_roughness: { label: "Низкая шероховатость", hint: "Ra ≤ 0,8 мкм (Rz ≤ 6,3): финишная обработка" },
  high_hardness: { label: "Высокая твёрдость", hint: "≥ 45 HRC: закалка, обработка твёрдых поверхностей" },
  chemical_heat_treatment: { label: "Химико-термическая обработка", hint: "Цементация, азотирование: специальная печь" },
  tight_geometric: { label: "Жёсткий допуск формы", hint: "≤ 0,02 мм: одна установка, координатное оборудование" },
  kit_tail: { label: "«Хвост» комплекта", hint: "Среди 10 % самых жёстких значений комплекта, хотя порогов общих правил не переходит" },
  rare_in_kit: { label: "Редкое в комплекте", hint: "Требование встречается у одной детали — отдельный передел" },
};
