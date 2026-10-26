"""Settings from configs/*.yaml (ADR-0012): checked on load, hashed into the report, no thresholds left in code."""

import ast
import shutil
from pathlib import Path

import pytest
import yaml
from kdcheck.config import ConfigError, config_dir, load_settings, settings_sha256

ROOT = Path(__file__).resolve().parents[3]
CORE = ROOT / "packages" / "kdcheck" / "src" / "kdcheck"

# Numbers that stay in code: facts of file formats, units, PDF and Tesseract, rounding and display.
# Any other number tunes the results and belongs in configs/*.yaml.
KEPT: dict[str, dict[float, str]] = {
    "cli.py": {12: "length of a random kit id", 70: "width of the progress line in the terminal"},
    "config.py": {4: "depth of this file below the repository root", 100: "top of the Tesseract confidence scale"},
    "ingest/document.py": {
        72: "points per inch",
        25.4: "millimetres per inch",
        4: "digits of a bbox share; length of the TIFF signature",
        10: "length of a document id",
        1024: "where a PDF signature may start",
        2048: "bytes read to detect the format",
        157: "length of a shortened problem text",
        160: "length of a problem text",
    },
    "ingest/pdfium_backend.py": {
        90: "page rotation of the PDF format",
        180: "page rotation of the PDF format; angles modulo a half turn",
        270: "page rotation of the PDF format",
        1e-6: "zero of a matrix term in floating point",
    },
    "ingest/pymupdf_backend.py": {180: "angles modulo a half turn"},
    "ocr/tesseract.py": {100: "top of the Tesseract confidence scale"},
    "params/parameters.py": {80: "length of a coating name in the report"},
    "pipeline.py": {
        0.6: "share of the progress bar",
        0.25: "share of the progress bar",
        0.87: "share of the progress bar",
        0.93: "share of the progress bar",
        0.97: "share of the progress bar",
        4: "digits of the completeness ratio",
    },
    "render.py": {2048: "bytes read to detect the format", 16: "grey levels of a page image"},
    "vlm/client.py": {12: "hex digits of a prompt hash in the report"},
}
SMALL = {0, 1, 2, 3}  # indices of a bbox (x0, y0, x1, y1), halves and pairs
NOT_CHECKED = ("report/exports.py", "schemas/")  # the look of the exports; bounds of the contract


def numbers(source: str) -> list[tuple[int, float]]:
    """Numeric literals of a module other than 0–3, those inside f-strings too: (line, value).

    The syntax tree sees into f-strings on every Python version; tokens do only from 3.12 (PEP 701).
    """
    found = [
        (node.lineno, node.col_offset, node.value)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
        and node.value not in SMALL
    ]
    return [(line, value) for line, _, value in sorted(found)]


def test_finder_sees_a_threshold():
    assert numbers("if quality['contrast'] < 18 or x > 0.5 * y:\n    return items[2]\n") == [(1, 18), (1, 0.5)]
    assert numbers("label = f'{text[:70]:<70} {ratio:.0%}'\n") == [(1, 70)]  # a format spec is text


def test_no_numeric_thresholds_in_code():
    stray = []
    for path in sorted(CORE.rglob("*.py")):
        name = path.relative_to(CORE).as_posix()
        if name.startswith(NOT_CHECKED):
            continue
        kept = KEPT.get(name, {})
        stray += [f"{name}:{line}: {value}" for line, value in numbers(path.read_text("utf-8")) if value not in kept]
    assert stray == [], "move these numbers to configs/*.yaml or explain them in KEPT"


def _copy(tmp_path: Path) -> Path:
    target = tmp_path / "configs"
    shutil.copytree(ROOT / "configs", target)
    return target


def _rewrite(path: Path, old: str, new: str) -> None:
    text = path.read_text("utf-8")
    assert text.count(old) == 1, old
    path.write_text(text.replace(old, new), "utf-8")


def test_committed_settings_load_from_the_checkout(monkeypatch):
    monkeypatch.delenv("ARM_CONFIG_DIR", raising=False)
    assert config_dir() == ROOT / "configs"
    settings = load_settings()
    assert settings.forms.title_block.forms[1].cells.designation == (65, 0, 185, 15)
    assert len(settings_sha256(settings)) == 64


def test_the_hash_follows_values_not_layout(tmp_path):
    config = _copy(tmp_path)
    before = settings_sha256(load_settings(config))
    for path in config.glob("*.yaml"):  # no comments, another layout and quoting
        data = yaml.safe_load(path.read_text("utf-8"))
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), "utf-8")
    assert settings_sha256(load_settings(config)) == before
    _rewrite(config / "criticality.yaml", "max_it_grade: 6", "max_it_grade: 7")
    assert settings_sha256(load_settings(config)) != before


def test_config_dir_comes_from_the_environment(tmp_path, monkeypatch):
    config = _copy(tmp_path)
    _rewrite(config / "ingest.yaml", "max_pages_per_file: 500", "max_pages_per_file: 9")
    monkeypatch.setenv("ARM_CONFIG_DIR", str(config))
    monkeypatch.delenv("ARM_MAX_PAGES", raising=False)
    assert load_settings().ingest.max_pages_per_file == 9


def test_unknown_key_names_the_file_and_the_field(tmp_path):
    config = _copy(tmp_path)
    _rewrite(config / "ingest.yaml", "render_dpi: 200", "render_dpi: 200\nrender_dpl: 300")
    with pytest.raises(ConfigError, match=r"ingest\.yaml: render_dpl: Extra inputs are not permitted"):
        load_settings(config)


def test_wrong_value_names_the_file_and_the_field(tmp_path):
    config = _copy(tmp_path)
    _rewrite(config / "criticality.yaml", "score: 0.55", "score: 5.5")
    with pytest.raises(ConfigError, match=r"criticality\.yaml: rare_in_kit\.score"):
        load_settings(config)


def test_missing_file_says_where_settings_come_from(tmp_path):
    config = _copy(tmp_path)
    (config / "forms.yaml").unlink()
    with pytest.raises(ConfigError, match="Нет файла настроек .*forms.yaml: укажите каталог настроек"):
        load_settings(config)


def test_files_must_agree(tmp_path):
    config = _copy(tmp_path)
    _rewrite(config / "criticality.yaml", "Нитроцементация, ", "Нитроцементация, Борирование, ")
    with pytest.raises(ConfigError, match=r"\['Борирование'\] нет среди heat_treatments"):
        load_settings(config)


def test_environment_overrides_single_values(monkeypatch):
    monkeypatch.setenv("ARM_OCR", "0")
    monkeypatch.setenv("ARM_PDF_BACKEND", "pymupdf")
    monkeypatch.setenv("ARM_MAX_PAGES", "7")
    settings = load_settings()
    assert settings.models.ocr.enabled is False
    assert settings.models.pdf.backend == "pymupdf"
    assert settings.ingest.max_pages_per_file == 7
    explicit = load_settings(use_ocr=True, pdf_backend="pdfium", max_pages=0)  # arguments win
    assert (explicit.models.ocr.enabled, explicit.models.pdf.backend, explicit.ingest.max_pages_per_file) == (
        True,
        "pdfium",
        0,
    )
    assert settings_sha256(settings) != settings_sha256(explicit)
    monkeypatch.setenv("ARM_MAX_PAGES", "all")
    with pytest.raises(ConfigError, match="ARM_MAX_PAGES: ожидается целое число"):
        load_settings()
    monkeypatch.setenv("ARM_MAX_PAGES", "0")
    monkeypatch.setenv("ARM_PDF_BACKEND", "acrobat")
    with pytest.raises(ConfigError, match=r"models\.yaml: pdf\.backend"):
        load_settings()


def test_a_model_server_on_another_machine_needs_consent(tmp_path):
    configs = _copy(tmp_path)
    _rewrite(configs / "models.yaml", "base_url: http://127.0.0.1:1234/v1", "base_url: http://10.0.0.5:1234/v1")
    with pytest.raises(ConfigError, match=r"models\.yaml: vlm: .*10\.0\.0\.5.*allow_remote: true"):
        load_settings(configs)
    _rewrite(configs / "models.yaml", "allow_remote: false", "allow_remote: true")
    assert load_settings(configs).models.vlm.base_url == "http://10.0.0.5:1234/v1"


def test_the_model_is_switched_on_from_the_environment(monkeypatch):
    monkeypatch.setenv("ARM_VLM", "1")
    monkeypatch.setenv("ARM_VLM_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("ARM_VLM_MODEL", "qwen3.6:27b")
    vlm = load_settings().models.vlm
    assert (vlm.enabled, vlm.base_url, vlm.model) == (True, "http://localhost:11434/v1", "qwen3.6:27b")
    assert load_settings(use_vlm=False).models.vlm.enabled is False  # an argument wins
    monkeypatch.setenv("ARM_VLM_URL", "https://models.example.com/v1")
    with pytest.raises(ConfigError, match="allow_remote"):
        load_settings()
