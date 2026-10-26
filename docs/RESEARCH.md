# Обзор: готовые решения, модели, данные

Версия 2 от 26.10.2026: список мультимодальных моделей с открытыми весами.

Готового решения под ЕСКД не нашли. Ближайшие аналоги — зарубежные сервисы чтения чертежей (Werk24, CADDi) и исследования по чертежам ISO и ASME. Из них берём подходы и компоненты; специфику ЕСКД — основную надпись, спецификацию, обозначения — закрываем шаблонами и правилами.

## Коммерческие решения

| Решение | Что делает | Что берём | Ограничения |
| --- | --- | --- | --- |
| [Werk24](https://werk24.io/) | API чтения чертежей: основная надпись, материалы, допуски, состав → JSON ([про основную надпись](https://werk24.io/blog/read-title-block-intelligently)) | Набор полей и структура ответа как ориентир | Зарубежное облако; ЕСКД не заявлена |
| [CADDi Drawer](https://us.caddi.com/product-overview) | OCR всех надписей PDF-чертежа, поиск по тексту и похожей геометрии ([FAQ](https://us.caddi.com/faq)) | Идея единой базы данных по чертежам | SaaS |
| [InspectionXpert](https://help.inspectionxpert.com/ocr-extraction-modes), [High QA](https://www.highqa.com/?p=19902), [1factory](https://1factory.com/pdf-drawing-auto-ballooning-software.html) | Извлечение размеров и GD&T из PDF для первой инспекции | Состав параметров Блока 2 | Ручное выделение областей, ASME |
| [SolidWorks Inspection](https://help.solidworks.com/2021/English/SWInspection/t_extracting_gdts.htm) | Извлечение размеров из PDF с текстовым слоем | Подтверждение: векторный PDF читается без OCR, качество зависит от САПР | Только своя экосистема |
| [«Цифровой нормоконтроль»](https://www.normacs.info/news/77904), Мосстройинформ | Пилот ИИ-проверки проектной документации по ГОСТ Р 21.101-2020 | Российский прецедент | Строительство, не ЕСКД |
| [Диджитал Клаудс](https://workspace.ru/cases/anonimizaciya-chertezhey/) | Анонимизация чертежей: поиск основной надписи по ключевым словам с поворотом листа, детекция таблиц нейросетью | Приём «поворачивать лист, пока не найдена основная надпись» | Кейс без кода |

## Исследования и открытый код

| Работа | Подход | Результат по данным авторов | Что берём |
| --- | --- | --- | --- |
| [eDOCr2](https://www.mdpi.com/2075-1702/13/3/254), Machines, 2025; [код](https://github.com/javvi51/edocr2), MIT | Сегментация чертежа (основная надпись, размеры, рамки допусков) + OCR + VLM (Qwen2-VL-7B, GPT-4o) для проверки | Полнота текста 93,75 %, CER < 1 % | Обучение распознавателей на синтетике; кандидат для размеров и GD&T |
| [Khan et al., 2024](https://arxiv.org/abs/2411.03707) | Дообучение Florence-2 (0,23B) на 400 чертежах для GD&T | Точнее GPT-4o и Claude 3.5 Sonnet без дообучения | Малая VLM с дообучением работает |
| [Khan et al., 2025](https://arxiv.org/abs/2505.01530) | YOLOv11-OBB на 9 классов (GD&T, основная надпись, примечания, шероховатость…) → вырезки → Donut → JSON | — | Схема «детектор → вырезка → VLM» |
| [Khan et al., 2025–2026](https://arxiv.org/abs/2510.21862), ICIEA 2026 | YOLOv11 (виды, основная надпись, примечания) + две Donut-VLM: текстовая и числовая | F1 0,963 на числовых аннотациях; 0,672 на основной надписи и примечаниях | Основная надпись — слабое место VLM без схемы → у нас шаблоны ГОСТ |
| [MechVQA](https://arxiv.org/abs/2605.30794), ICML 2026 | Бенчмарк понимания машиностроительных чертежей: ~3,3 тыс. чертежей, 21 тыс. вопросов | Специализированная модель на 7,57 п. п. лучше сильнейшей закрытой | Методика оценки VLM на поле чертежа |
| [Тампере, 2025](https://trepo.tuni.fi/handle/10024/232922) | YOLOv11 для таблиц состава + GPT-4o | > 95 % обнаружения таблиц состава на 100+ чертежах | Подход к таблицам состава |
| [Lombardi et al., 2025](https://arxiv.org/abs/2504.08645); [EC3 2026](https://ec-3.org/publication/ec32026_252/) | Основная надпись строительных чертежей: детектор + GPT-4o; Qwen2.5-VL | — | Сравнение облачной и открытой VLM |

## Модели: кандидаты для бенчмарка

| Задача | Кандидаты | Лицензия | Комментарий |
| --- | --- | --- | --- |
| Текст и линии векторного PDF | pdfplumber, pypdfium2; PyMuPDF | MIT; Apache-2.0 или BSD; AGPL-3.0 | pdfplumber отдаёт символы и линии с координатами — сетка спецификации без OCR |
| OCR печатного текста | Tesseract 5 (rus), PP-OCR (PaddleOCR), EasyOCR | Apache-2.0 | Быстро, на CPU; шрифт ГОСТ может потребовать дообучения |
| Документные VLM-OCR | PaddleOCR-VL-1.6, Qianfan-OCR, dots.mocr и другие — [полный список](#документные-ocr-модели) | Apache-2.0, MIT; у части — ограничения | PaddleOCR-VL: 109 языков, включая русский; № 1 в [обзоре Roboflow](https://roboflow.com/blog/best-open-source-ocr-models) — 96,34 % на OmniDocBench v1.6 |
| VLM общего назначения | Qwen3.5–3.8, Gemma 4, MiniCPM-V 4.7 и другие — [полный список](#мультимодальные-модели-с-открытыми-весами) | Apache-2.0 | Спорные ячейки, нестандартные бланки, поле чертежа; дообучение LoRA |
| Специализированные для чертежей | eDOCr2, Florence-2, Donut | MIT | Размеры, допуски, GD&T |
| Облачный OCR | [Yandex Vision OCR](https://yandex.cloud/en/docs/vision/concepts/ocr): модели `page`, `table`, `handwritten`, `markdown` | Коммерческий | `table` и `handwritten` — русский и английский; только с разрешения АРМ |
| LLM для техтребований | Qwen3.5, [GigaChat 3](https://habr.com/ru/companies/sberdevices/articles/968904/) (открытые веса) | Apache-2.0; MIT | Структурированный вывод по JSON-схеме |
| Детекция областей | RT-DETR, D-FINE; YOLO11 | Apache-2.0; AGPL-3.0 | YOLO — только по отдельному решению из-за AGPL |
| Нечёткое сравнение | rapidfuzz | MIT | Наименования |
| Разметка | Label Studio, CVAT | Apache-2.0, MIT | Эталонная выборка |

Лицензии перепроверяем перед включением в поставку заказчику.

## Мультимодальные модели с открытыми весами

Список на 26.10.2026: модели, которые принимают картинку и текст и выдают текст. Дата публикации, число параметров, лицензия и языки взяты из карточек моделей на Hugging Face. Русский отмечен, только если он назван в карточке; «многоязычная» — карточка не перечисляет языки. Качество на наших листах не измерено: это кандидаты для бенчмарка этапа 6. Лидерство в OmniDocBench на него не переносится: в этом бенчмарке китайские и английские документы.

Локально модель работает за OpenAI-совместимым сервером: LM Studio или llama.cpp на ноутбуке (сборки GGUF и MLX), vLLM на сервере с GPU. Так ядро вызывает любую модель одним адаптером.

### Документные OCR-модели

Малые модели, обученные читать страницы документов: таблицы, формулы, разметку страницы. Кандидаты для ячеек основной надписи и спецификации на сканах.

| Модель | Дата | Параметры | Лицензия | Русский | Сборки | Комментарий |
| --- | --- | --- | --- | --- | --- | --- |
| [PaddleOCR-VL-1.6](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6), Baidu | 05.2026 | 0,96B | Apache-2.0 | многоязычная; первая версия заявляла 109 языков, включая русский | GGUF от авторов, MLX | Главный кандидат из RESEARCH v1, теперь с официальной GGUF |
| [Qianfan-OCR](https://huggingface.co/baidu/Qianfan-OCR), Baidu | 03.2026 | 4,7B | Apache-2.0 | да: 192 языка, кириллица | GGUF | Разбор страницы и вопросы по документу в одной модели |
| [Unlimited-OCR](https://huggingface.co/baidu/Unlimited-OCR), Baidu | 06.2026 | 3,3B | MIT | многоязычная | GGUF | |
| [dots.mocr](https://huggingface.co/dots-studio/dots.mocr), rednote | 03.2026 | 3,0B | MIT | да, пример на русском | GGUF | Графику переводит в SVG |
| [DeepSeek-OCR-2](https://huggingface.co/deepseek-ai/DeepSeek-OCR-2) | 01.2026 | 3,4B | Apache-2.0 | многоязычная | GGUF | |
| [LightOnOCR-3](https://huggingface.co/lightonai/LightOnOCR-3-1B) 0,8B, 1B, 4B | 10.2026 | 1,0B; 4,5B | Apache-2.0 | не указан | GGUF | |
| [TeleOCR](https://huggingface.co/XingChen-AGI/TeleOCR) | 08.2026 | 1,4B | Apache-2.0 | нет: китайский и английский | GGUF | |
| [OvisOCR2](https://huggingface.co/ATH-MaaS/OvisOCR2), [OvisOCR3](https://huggingface.co/ATH-MaaS/OvisOCR3) | 07, 10.2026 | 0,85B | Apache-2.0 | не указан | GGUF | |
| [MinerU2.5-Pro](https://huggingface.co/opendatalab/MinerU2.5-Pro-2605-1.2B) | 05.2026 | 1,2B | Apache-2.0 | нет: китайский и английский | GGUF | Сам инструмент MinerU — AGPL-3.0, модель — Apache-2.0 |
| [NVIDIA Nemotron-Parse 2.0](https://huggingface.co/nvidia/NVIDIA-Nemotron-Parse-2.0), [3.5-Parse](https://huggingface.co/nvidia/Nemotron-3.5-Parse) | 06, 09.2026 | 0,9B; 0,6B | OpenMDW-1.1 | кириллица | MLX | |
| [HunyuanOCR](https://huggingface.co/tencent/HunyuanOCR), Tencent | 11.2025 | 1,1B | Tencent Hunyuan Community: не действует в ЕС, Великобритании и Южной Корее | многоязычная | GGUF | Лицензия — по отдельному решению |
| [Chandra OCR 2](https://huggingface.co/datalab-to/chandra-ocr-2), [Surya OCR 2](https://huggingface.co/datalab-to/surya-ocr-2), Datalab | 03, 05.2026 | 5,3B; 0,7B | изменённая OpenRAIL-M, коммерческое использование ограничено | да: 90+ языков | GGUF | Лицензия — по отдельному решению |
| [Nanonets-OCR2-3B](https://huggingface.co/nanonets/Nanonets-OCR2-3B) | 10.2025 | 3,8B | не указана; основа Qwen2.5-VL-3B под исследовательской лицензией | да | GGUF | Лицензию выяснять до проб |
| [olmOCR-2](https://huggingface.co/allenai/olmOCR-2-7B-1025), AI2 | 10.2025 | 8,3B | Apache-2.0 | нет: английский | GGUF | |
| [granite-docling-258M](https://huggingface.co/ibm-granite/granite-docling-258M), IBM | 05.2025 | 0,26B | Apache-2.0 | нет: английский | GGUF | |

### VLM общего назначения до 15B

Работают на ноутбуке. Кандидаты для нестандартных бланков, спорных ячеек и вопросов к листу целиком.

| Модель | Дата | Параметры | Лицензия | Русский | Сборки | Комментарий |
| --- | --- | --- | --- | --- | --- | --- |
| [Qwen3.5](https://huggingface.co/Qwen/Qwen3.5-9B) 0,8B, 2B, 4B, 9B | 02.2026 | 0,9–9,7B | Apache-2.0 | да: 201 язык | GGUF, MLX | Нативно мультимодальные; основа MiniCPM-V 4.7 и LightOnOCR-3 |
| [Qwen3-VL-8B](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct) | 10.2025 | 8,8B | Apache-2.0 | OCR на 32 языках | GGUF | |
| [Gemma 4](https://huggingface.co/google/gemma-4-E4B-it) E2B, E4B, 12B | 03–05.2026 | 5,1B; 8,0B; 12B | Apache-2.0 | 140+ языков в обучении | GGUF | E4B скачана в LM Studio |
| [MiniCPM-V 4.7](https://huggingface.co/openbmb/MiniCPM-V-4.7-1B) 1B, [4.6](https://huggingface.co/openbmb/MiniCPM-V-4.6) | 10, 04.2026 | 1,6B; 1,3B | Apache-2.0 | не указан | GGUF | |
| [InternVL3.5](https://huggingface.co/OpenGVLab/InternVL3_5-8B) 1B–8B | 08.2025 | 1–8,5B | Apache-2.0 | многоязычная | GGUF | |
| [Mage-VL](https://huggingface.co/microsoft/Mage-VL), Microsoft | 07.2026 | 4,7B | Apache-2.0 | не указан | GGUF, MLX | |
| [granite-vision-4.1-4b](https://huggingface.co/ibm-granite/granite-vision-4.1-4b), IBM | 04.2026 | 4B | Apache-2.0 | нет: английский | — | Документы и графики |
| [Ministral 3](https://huggingface.co/mistralai/Ministral-3-8B-Instruct-2512) 3B, 8B, 14B | 12.2025 | 3–14B | Apache-2.0 | нет в списке языков | GGUF | |
| [Step3-VL-10B](https://huggingface.co/stepfun-ai/Step3-VL-10B) | 01.2026 | 10,2B | Apache-2.0 | не указан | — | |
| [GLM-4.6V-Flash](https://huggingface.co/zai-org/GLM-4.6V-Flash) | 12.2025 | 10,3B | MIT | нет: китайский и английский | GGUF | |
| [Phi-4-reasoning-vision-15B](https://huggingface.co/microsoft/Phi-4-reasoning-vision-15B) | 01.2026 | 15,1B | MIT | нет: английский | — | |
| [MiMo-VL-7B](https://huggingface.co/XiaomiMiMo/MiMo-VL-7B-RL-2508) | 08.2025 | 8,3B | MIT | не указан | GGUF | |
| [Molmo2-8B](https://huggingface.co/allenai/Molmo2-8B), AI2 | 12.2025 | 8,7B | Apache-2.0 | нет: английский | — | Указывает точки на изображении |
| [LFM2.5-VL](https://huggingface.co/LiquidAI/LFM2.5-VL-3B) 1,6B, 3B | 01, 08.2026 | 1,6B; 3,1B | LFM Open License: бесплатно до порога выручки | да (3B) | GGUF, MLX | Лицензия — по отдельному решению |
| [Florence-2](https://huggingface.co/microsoft/Florence-2-large), Microsoft | 06.2024 | 0,8B | MIT | нет | — | Дообучение на чертежах: Khan et al., 2024 |
| [Vikhr-2-VL-2B](https://huggingface.co/Vikhrmodels/Vikhr-2-VL-2b-Instruct-experimental) | 11.2024 | 2,2B | Apache-2.0 | да, русскоязычная | — | Экспериментальная; других открытых русскоязычных VLM не нашли: GigaChat 3.1 и YandexGPT 5 Lite открыты только текстовыми |

### VLM общего назначения 15–40B

Одна GPU на 24–48 ГБ или Mac с 32 ГБ в 4 битах.

| Модель | Дата | Параметры | Лицензия | Русский | Сборки | Комментарий |
| --- | --- | --- | --- | --- | --- | --- |
| [Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B) | 08.2026 | 27,8B | Apache-2.0 | да: 201 язык (семейство Qwen3.5) | GGUF, MLX | Новейшая плотная модель Qwen |
| [Qwen3.6-27B](https://huggingface.co/Qwen/Qwen3.6-27B), [Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) | 04.2026 | 27,8B; 36B, из них 3B активных | Apache-2.0 | да (семейство Qwen3.5) | GGUF, MLX | 27B скачана в LM Studio |
| [Gemma 4 26B-A4B](https://huggingface.co/google/gemma-4-26B-A4B-it), [31B](https://huggingface.co/google/gemma-4-31B-it) | 03.2026 | 25,8B; 31,3B | Apache-2.0 | 140+ языков в обучении | GGUF | |
| [MiniCPM-V 4.7 35B-A3B](https://huggingface.co/openbmb/MiniCPM-V-4.7-35B-A3B) | 10.2026 | 35,2B | Apache-2.0 | не указан | GGUF | |
| [Ovis2.6-30B-A3B](https://huggingface.co/ATH-MaaS/Ovis2.6-30B-A3B) | 02.2026 | 31,4B | Apache-2.0 | не указан | — | |
| [ERNIE-4.5-VL-28B-A3B](https://huggingface.co/baidu/ERNIE-4.5-VL-28B-A3B-Thinking), Baidu | 11.2025 | 29,7B | Apache-2.0 | нет: китайский и английский | — | |
| [Kimi-VL-A3B](https://huggingface.co/moonshotai/Kimi-VL-A3B-Instruct) | 04.2025 | 16,4B | MIT | не указан | GGUF | |
| [Nemotron Nano 12B v2 VL](https://huggingface.co/nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-BF16), NVIDIA | 10.2025 | 13,2B | NVIDIA Open Model License | не указан | — | |

### Крупные модели

Только сервер с несколькими GPU. Нужны, если малые модели не справятся, и как эталон при разборе спорных случаев.

| Модель | Дата | Параметры | Лицензия | Русский |
| --- | --- | --- | --- | --- |
| [Mistral Small 4](https://huggingface.co/mistralai/Mistral-Small-4-119B-2603) | 2026 | 119B | Apache-2.0 | да |
| [Nemotron 3.5 Super VL 120B-A12B](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-BF16) | 10.2026 | 124B | OpenMDW-1.1 | упомянут в карточке |
| [Qwen3.8-Flash-Next](https://huggingface.co/Qwen/Qwen3.8-Flash-Next) | 08.2026 | 180B | Qwen Community License 1.0 | да |
| [GLM-5.3-Flash](https://huggingface.co/zai-org/GLM-5.3-Flash) | 08.2026 | 321B | MIT | не указан |
| [DeepSeek-V4-Flash-Vision-Exp](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp) | 08.2026 | 305B | MIT | не указан |
| [Llama 4 Scout](https://huggingface.co/meta-llama/Llama-4-Scout-17B-16E-Instruct) | 04.2025 | 109B | Llama 4 Community License | нет в списке языков |

Не годятся по лицензии: Aya Vision (CC-BY-NC, только некоммерческое использование), Apple LensVLM-9B (только исследования), Tencent Youtu-Parsing и HyperCLOVA X SEED Vision (собственные лицензии с ограничениями).

Специальных открытых моделей для чертежей ЕСКД нет. Для чертежей ISO и ASME есть конвейер eDOCr2 (MIT) и дообученные Florence-2 и Donut из исследований (см. выше).

Порядок проб: сначала документные OCR-модели под Apache-2.0 и MIT с русским языком (PaddleOCR-VL-1.6, Qianfan-OCR, dots.mocr) на вырезках ячеек, затем VLM общего назначения (Qwen3.6/3.8-27B, Gemma 4) на листе целиком. Мерим на эталонных вырезках этапа 6.

## Данные: где брать

| Источник | Что | Для чего | Риски |
| --- | --- | --- | --- |
| Свой генератор ЕСКД | Основные надписи (формы 1, 2, 2а), спецификации, техтребования с разметкой. Основа: [typst-gost/stamp-eskd-spds](https://github.com/typst-gost/stamp-eskd-spds) (BSD-3), [ESKDX](https://github.com/DmNosachev/eskdx) (LaTeX), [DocGOST](https://github.com/vitalii-nechaev/DocGOST) (спецификации из KiCad и Altium) | Обучение, метрики извлечения | Разрыв с реальностью → деградации и проверка на реальных листах |
| [Augraphy](https://github.com/sparkfish/augraphy) | Деградации «печать — скан»: шум, выцветание, сгибы, тени | Реалистичные сканы из синтетики | — |
| Синтетические комплекты с мутациями | Дерево изделия, согласованные документы, внесённые ошибки | Метрики полноты Блока 1 | Не покрывают неизвестные типы ошибок |
| Открытые чертежи ЕСКД | Учебные пособия вузов, публичные каталоги и форумы | Реализм, эталонная выборка | Лицензии: только внутренняя оценка, не публикуем |
| Наборы ISO и ASME | Тестовые чертежи [eDOCr2](https://github.com/javvi51/edocr2); данные Khan et al. и MechVQA — доступность уточнить | Предобучение детекторов размеров и GD&T | Другие стандарты оформления |
| Данные АРМ | Реальные комплекты, исходники САПР, журнал замечаний | Эталон, калибровка, демо | NDA |

## Нормативная база

| Стандарт | О чём | Где нужен |
| --- | --- | --- |
| ГОСТ Р 2.104-2023 — заменил ГОСТ 2.104-2006 с 01.03.2024 ([NormaCS](https://www.normacs.info/answers/6023)) | Основные надписи | Шаблоны полей; в комплектах возможны обе редакции |
| ГОСТ Р 2.106-2019 — ГОСТ 2.106-96 в РФ не применяется ([NormaCS](https://www.normacs.info/discussions/2336)) | Текстовые документы, спецификация | Разбор спецификаций |
| ГОСТ 2.102 | Виды и комплектность КД, коды документов | Правила комплектности |
| ГОСТ 2.103 | Стадии разработки, литеры | Правила литер |
| ГОСТ 2.109 | Основные требования к чертежам | Случаи, когда чертёж детали не выпускают |
| ГОСТ 2.113 | Групповые и базовые КД | Исполнения |
| ГОСТ 2.201 | Обозначение изделий и документов | Формат обозначений |
| ГОСТ 2.301, 2.304 | Форматы, шрифты | Формат листа, синтетика |
| ГОСТ 2.307, 2.308, 2.309, 2.316 | Размеры и отклонения, допуски формы, шероховатость, надписи и техтребования | Блок 2 |
| ГОСТ 2.503 | Внесение изменений | Номер изменения |
| ГОСТ 25346, 25347, 30893.1 | Допуски и посадки, общие допуски | Числовые отклонения |
| ГОСТ 2789 | Параметры шероховатости | Ra и Rz |
| ГОСТ 9.306 | Обозначения покрытий | Покрытия |

## Выводы для проекта

1. Под ЕСКД готового решения нет. Наше преимущество — шаблоны основной надписи и спецификации плюс правила комплектности.
2. VLM без схемы плохо извлекают основную надпись (F1 0,67), а после дообучения хорошо — числовые аннотации (F1 0,96). Отсюда: основная надпись — шаблон и OCR ячеек, поле чертежа — дообученная VLM как расширение.
3. Сильнейший открытый кандидат для сканов на русском — PaddleOCR-VL. Проверяем на своей эталонной выборке: русских бенчмарков для него нет.
4. Если АРМ разрешит облако, Yandex Vision OCR с моделями `table` и `handwritten` — сильный вариант для русского текста и таблиц.
5. Лицензии важны для передачи кода: AGPL-компоненты держим за адаптерами.
