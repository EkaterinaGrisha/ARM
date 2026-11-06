# Промпты

Промпты моделей — версионные файлы: `<задача>.v<N>.txt`. Изменённый промпт — новый файл со следующей версией, старый остаётся, чтобы прежние отчёты можно было повторить. Отчёт записывает имя промпта и начало его SHA-256 в версию движка.

| Файл | Задача | Где используется |
| --- | --- | --- |
| `title_block_cell.v1.txt` | Текст одной ячейки основной надписи; `{label}` — название ячейки из `vlm.fields` в `configs/models.yaml` | `kdcheck.vlm`, [ADR-0014](../docs/adr/0014-local-vlm.md) |
| `bench_sheet_fields.v1.txt` | Поля основной надписи со всего листа, ответ — JSON | `eval/vlm_bench.py` |
| `bench_requirements.v1.txt` | Текст блока технических требований, ответ — JSON | `eval/vlm_bench.py` |
| `bench_ocr_plain.v1.txt` | Весь текст изображения для OCR-модели, которая принимает инструкцию | `eval/vlm_bench.py` |
| `bench_ocr_paddle.v1.txt` | Команда распознавания PaddleOCR-VL | `eval/vlm_bench.py` |
| `bench_ocr_markdown.v1.txt` | Распознавание в Markdown — промпт из карточки Qianfan-OCR | `eval/vlm_bench.py` |
| `bench_ocr_lighton.v1.txt` | Пустой: LightOnOCR-3 без инструкции переписывает страницу, модель получает только изображение | `eval/vlm_bench.py` |
| `bench_ocr_dots.v1.txt` | Текст изображения — промпт `prompt_ocr` из репозитория dots.mocr | `eval/vlm_bench.py` |
| `bench_ocr_unlimited.v1.txt` | Разбор документа — промпт из карточки Unlimited-OCR; токен `<image>` в тексте, потому что шаблон чата модели его не ставит | `eval/vlm_bench.py` |

Каталог задаёт `ARM_PROMPTS_DIR`, по умолчанию — `prompts/` рабочей копии.
