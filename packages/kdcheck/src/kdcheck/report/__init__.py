"""Report forms: JSON for integration, Excel and HTML for the technologist."""

from pathlib import Path

from kdcheck.report.exports import to_html, to_xlsx
from kdcheck.schemas import Report

__all__ = ["to_html", "to_xlsx", "write_report"]


def write_report(report: Report, out_dir: Path) -> None:
    """Write report.json, report.xlsx and report.html into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(report.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    (out_dir / "report.xlsx").write_bytes(to_xlsx(report))
    (out_dir / "report.html").write_text(to_html(report), encoding="utf-8")
