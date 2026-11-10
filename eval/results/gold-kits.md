# Блок 1 на размеченных комплектах

Получено `uv run python eval/gold.py kits` 2026-11-10; Darwin arm64, Python 3.11.15; OCR включён. Комплекты — учебные с GitHub из корпуса открытых чертежей, разметка — [eval/gold/github](../gold/github). В разметке — типы расхождений, а не правила ядра, поэтому расхождение найдено, если в отчёте есть тот же тип с тем же обозначением после нормализации. Пропуски и лишние по комплектам — в [gold-kits.json](gold-kits.json).

| Комплект | Элементов дерева | В разметке | Найдено | Пропущено | Лишних | Лишние по типам |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| github/AiuRat_breadboard-power-qcpd | 0 | 11 | 3 | 8 | 0 | — |
| github/UshliyOrk_Ucheba | 13 | 5 | 5 | 0 | 7 | `duplicate_designation` 3, `name_mismatch` 4 |
| github/snowlue_ITMO_works | 12 | 7 | 6 | 1 | 1 | `revision_mismatch` 1 |
