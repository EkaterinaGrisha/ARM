# Contributing

Project: batch completeness check and analysis of design documentation (ESKD), a case by ООО «Априорные Решения Машин» (ARM). Requirements: [docs/CASE.md](docs/CASE.md).

## Languages

1. **Code**: code, identifiers, docstrings, comments, commit messages, PR descriptions — English only.
2. **Human-facing text**: reports, summaries, articles and docs in `docs/` — Russian. Short, no polite preambles.

Domain data stays in Russian even inside code: GOST section names («Детали», «Сборочные единицы»), regexes for Cyrillic text, user-facing report strings. These are data, not comments.

## Sources of truth

| File | What |
| --- | --- |
| [docs/CASE.md](docs/CASE.md) | Full case text from Keys_ARM.pdf, verbatim: requirements and evaluation criteria |
| [docs/PLAN.md](docs/PLAN.md) | Work plan by stages: follow it, tick checkboxes, record deviations |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architecture, data contracts, API, decisions log |
| [docs/RESEARCH.md](docs/RESEARCH.md) | Existing solutions, models, datasets, licenses |

Before starting a task, find its stage in docs/PLAN.md. If the task changes the plan or the architecture, update the doc in the same PR. Architecture decisions are recorded as ADRs in `docs/adr/`, indexed by the «Решения» table in docs/ARCHITECTURE.md.

## Engineering rules

- **Core first.** All logic lives in the `kdcheck` Python package with a CLI. The API and the web only call the core; no business logic in the API or the frontend.
- **Contracts first.** Stage outputs are pydantic models in `kdcheck.schemas`. A schema change bumps `schema_version`, regenerates `docs/report.schema.json` and the web types (`scripts/export_schema.sh`) and updates the fixtures (`samples/`, `frontend/public/demo/report.json`).
- **Traceability.** Every extracted value carries `Evidence`: file, page, bbox, method, model version, confidence. No value without evidence.
- **Robustness.** One bad file or page never stops a run. Catch per file and per page, record a problem entry with a category, continue.
- **Rules before models.** Use GOST geometry, the PDF text layer and regexes where they work. Add OCR/VLM/LLM only with a measured gain on the gold set.
- **Config over code.** Thresholds, GOST templates, dictionaries and model choices live in `configs/*.yaml`.
- **Reproducibility.** Fixed seeds; model names and versions are written to the report; prompts are versioned files in `prompts/`, never inline strings.
- **Checks.** Each rule has an id (`B1.NAME.001`), a Russian message for the report, and tests with a positive and a negative case.
- **Evaluation.** The gold set in `eval/gold/` is frozen; never tune on the test split; every quality claim cites a metrics file.
- **Data safety.** The repo is public: customer data never goes into git (`data/`, `models/`, `out/` are ignored). Customer documents are never sent to external APIs unless the customer allowed it (config flag, off by default).
- **Licenses.** Prefer Apache-2.0/MIT/BSD. AGPL components (PyMuPDF, Ultralytics YOLO) only behind an adapter and after an explicit decision in docs/ARCHITECTURE.md. CI fails on an AGPL dependency in what ships, `backend/requirements.txt` (pip-licenses, `scripts/check_licenses.sh`).

## Stack

Python ≥ 3.11, uv, ruff (lint + format), mypy (strict for `kdcheck`), pytest. API: FastAPI; each job runs `kdcheck run` in a separate process with limits, no queue server (ADR-0006). Frontend: React, TypeScript, Vite in `frontend/`; report types are generated from the report JSON Schema (json-schema-to-typescript).

## Git

- Branch per task: `feat/…`, `fix/…`, `docs/…`, `exp/…` (experiments). No direct pushes to `main`.
- Conventional Commits in English, small PRs.

## Commands

```bash
uv sync --all-packages --all-extras   # Python environment from uv.lock
uv run pre-commit install             # ruff, lockfile and requirements checks on commit
uv run pytest                         # kdcheck, backend and synth tests
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports
sh scripts/export_requirements.sh     # after changing dependencies: refresh backend/requirements.txt
sh scripts/check_licenses.sh          # after changing dependencies: no AGPL in what ships
sh scripts/export_schema.sh           # after changing kdcheck.schemas: refresh docs/report.schema.json and web types
uv run python -m synth.demo_kit samples/demo_kit   # after changing the generator: regenerate the demo kit

cd frontend && npm ci && npm run typecheck && npm test && npm run build
```

CI (`.github/workflows/ci.yml`) runs the same checks on Ubuntu with Python 3.11 and 3.12 and on macOS with Python 3.11. Running the prototype: [README.md](README.md).

## Glossary (RU → identifier)

| RU | Identifier |
| --- | --- |
| Комплект КД | `doc_set` |
| Основная надпись | `title_block` |
| Спецификация / строка спецификации | `specification` / `spec_row` |
| Обозначение / наименование | `designation` / `name` |
| Литера / номер изменения | `litera` / `change_number` |
| Сборочная единица / деталь | `assembly` / `part` |
| Сборочный чертёж (СБ) | `assembly_drawing` |
| Деталь без чертежа (БЧ) | `no_drawing_part` |
| Перв. примен. / архивный номер | `first_use` / `archive_number` |
| Технические требования | `tech_requirements` |
| Квалитет / предельные отклонения | `it_grade` / `limit_deviations` |
| Шероховатость | `roughness` |
| Допуски формы и расположения | `gdt` |
| Термообработка / покрытие | `heat_treatment` / `coating` |
| Расхождение | `finding` |
| Дерево изделия | `product_tree` |
