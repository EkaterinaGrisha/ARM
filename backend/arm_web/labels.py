"""Русские подписи для значений перечислений отчёта (используются в экспорте)."""

DISCREPANCY_TYPES = {
    "missing_drawing": "Чертёж отсутствует",
    "orphan_drawing": "Чертёж не входит в спецификации",
    "name_mismatch": "Расхождение наименования",
    "duplicate_designation": "Дублирование обозначения",
    "revision_mismatch": "Расхождение литеры / изменения",
    "missing_specification": "Нет спецификации сборочной единицы",
}
SEVERITY = {"high": "Высокая", "medium": "Средняя", "low": "Низкая"}
DOCUMENT_STATUS = {
    "ok": "Обработан",
    "warning": "С замечаниями",
    "damaged": "Повреждён",
    "unreadable": "Нечитаемый скан",
    "foreign": "Посторонний документ",
    "unsupported": "Неподдерживаемый формат",
}
DOCUMENT_KIND = {
    "specification": "Спецификация",
    "assembly_drawing": "Сборочный чертёж",
    "part_drawing": "Чертёж детали",
    "drawing": "Чертёж",
    "other": "Прочий документ",
    "unknown": "Не определён",
}
ORIGIN = {"vector": "Векторный", "scan": "Скан", "mixed": "Смешанный", "unknown": "—"}
NODE_KIND = {
    "kit": "Комплект",
    "assembly": "Сборочная единица",
    "part": "Деталь",
    "standard": "Стандартное изделие",
    "other_item": "Прочее изделие",
    "material": "Материал",
    "document": "Документ",
    "unknown": "—",
}
NODE_STATUS = {"ok": "Есть", "missing": "Отсутствует", "warning": "Есть замечания", "not_required": "Не требуется"}
PARAMETER_CATEGORY = {
    "dimension_tolerance": "Допуск размера",
    "general_tolerance": "Общие допуски",
    "roughness": "Шероховатость",
    "geometric": "Допуск формы/расположения",
    "heat_treatment": "Термообработка",
    "hardness": "Твёрдость",
    "coating": "Покрытие",
    "other": "Прочее",
}
TITLE_FIELDS = {
    "designation": "Обозначение",
    "name": "Наименование",
    "material": "Материал",
    "mass": "Масса",
    "scale": "Масштаб",
    "litera": "Литера",
    "revision": "Изм.",
    "archive_number": "Арх. №",
    "first_use": "Перв. примен.",
    "sheet": "Лист",
    "sheets": "Листов",
}
