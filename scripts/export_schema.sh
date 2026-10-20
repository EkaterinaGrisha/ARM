#!/usr/bin/env sh
# Regenerate docs/report.schema.json from kdcheck.schemas. Tests fail while it is stale.
set -eu
uv run --frozen python -m kdcheck.schemas > docs/report.schema.json
