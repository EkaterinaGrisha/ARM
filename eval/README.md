# Оценка качества

Замеры, на которые ссылаются документы. Любая цифра о качестве берётся из файла в `results/`.

| Скрипт | Что меряет | Результат |
| --- | --- | --- |
| `pdf_backends.py` | Движки PDF на демо-комплекте: метрики по эталону и время на лист | [results/pdf-backends.md](results/pdf-backends.md) |
| `open_drawings.py` | Корпус открытых чертежей по реестру `open_drawings.csv`: `fetch` скачивает файлы в `data/open_drawings` (вне git) и сверяет sha256, `summary` считает документы, страницы и листы по периодам | [results/open-drawings.md](results/open-drawings.md) |
| `vlm_title_block.py` | Основная надпись сканов: OCR против OCR с мультимодальной моделью на локальном сервере ([ADR-0014](../docs/adr/0014-local-vlm.md)), по полям; значения модели и их точность, конфликты при проверке, время. `--real` — те же прогоны на реальных сканах эталона, с таблицей по листам | [results/vlm-title-block.md](results/vlm-title-block.md), [results/vlm-title-block-real.md](results/vlm-title-block-real.md) |
| `gold.py` | Эталон на реальных документах ([gold/README.md](gold/README.md)): `template` — черновик разметки комплекта или набора листов, `sheets` — листы набора из корпуса в `data/gold_sheets`, `check` — формат, пути по реестру и список листов, `agree` — согласие двух разметок по значениям | [results/gold-agreement.md](results/gold-agreement.md) |
| `synth_kits.py` | Блок 1 на синтетических комплектах с заложенными ошибками ([ADR-0013](../docs/adr/0013-synthetic-data.md)): полнота и точность по правилам и мутациям, ложные срабатывания на чистых комплектах, статусы проблемных файлов, причины расхождений | [results/synth-kits.md](results/synth-kits.md) |
| `scan_quality.py` | Растры и сканы `synth.degrade` ([ADR-0013](../docs/adr/0013-synthetic-data.md)): классы качества; одни и те же статистики изображения для реальных сканов эталона и синтетики каждого класса (разрешение, тушь и бумага, шум, штрих, крап, неровность фона, наклон) и ошибка оценки наклона; чтение основной надписи и строк спецификаций ядром с OCR по виду входа, классу, разрешению и виду пикселей | [results/scan-quality.md](results/scan-quality.md) |

Запуск из корня репозитория: `uv run python eval/pdf_backends.py`; PyMuPDF для сравнения ставится с `uv sync --all-packages --all-extras`. Цифры OCR зависят от версии Tesseract, а время — от машины: оба записаны в файл результата. После перегенерации демо-комплекта (`uv run python -m synth.demo_kit samples/demo_kit`) замер повторяют: сканы получаются новыми, и цифры OCR меняются.

`synth_kits.py` собирает корпус по сиду в `data/synth/kits` (вне git) и прогоняет ядро с OCR и без; комплекты, разметка и цифры повторяются на любой машине с тем же Tesseract. `scan_quality.py` так же собирает свой корпус в `data/synth/scan_kits`, а реальные сканы берёт из `data/gold_sheets` (`eval/gold.py sheets`).

Демо-комплект синтетический, чертежей-сканов в нём два. Поэтому эти замеры показывают, что ничего не сломалось, но не дают оценку качества. Её даст эталонная выборка (этап 8 плана).
