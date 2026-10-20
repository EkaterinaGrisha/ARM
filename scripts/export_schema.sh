#!/usr/bin/env sh
# Regenerate docs/report.schema.json from kdcheck.schemas and the web types from it. Tests fail while either is stale.
set -eu
uv run --frozen python -m kdcheck.schemas > docs/report.schema.json
npm --prefix frontend run --silent gen:types
