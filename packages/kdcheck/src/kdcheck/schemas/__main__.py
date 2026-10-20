"""Print the report JSON Schema: ``python -m kdcheck.schemas > docs/report.schema.json``."""

import sys

from kdcheck.schemas import report_json_schema

sys.stdout.write(report_json_schema())
