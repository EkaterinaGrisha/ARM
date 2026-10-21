#!/usr/bin/env sh
# Fail if a dependency that ships with the product is under the AGPL (ADR-0007).
# What ships is backend/requirements.txt: the Docker image and pip users install it. The development environment
# is not checked: it installs PyMuPDF (AGPL) on purpose, to test the rollback backend.
set -eu
env=$(mktemp -d)
trap 'rm -rf "$env"' EXIT
uv venv --quiet "$env"
uv pip install --quiet --python "$env/bin/python" -r backend/requirements.txt
uv run pip-licenses --python "$env/bin/python" --order license --partial-match --fail-on "AGPL;Affero"
