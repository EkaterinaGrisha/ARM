"""The designation of a title block decided against the designation of its file name (ADR-0022).

The file name is typed; the title block of a raster or a scan is read by OCR or the model, which misread a letter of
the code of the maker («КТПМ» for «КТРМ») and drop a thin «1». A drawing whose designation is misread misses its row,
and the report gets a drawing that is missing and one that is in no specification. When the reading and the file name
have the same digits, or differ by one digit dropped or doubled, the document takes the designation of its file name.
A digit read as another is never forgiven: the file may be named wrong, and a wrong name must not hide what the title
block says. The reading keeps its ``raw`` and its cell as the evidence and gets the ``conflict`` status, as a row
checked against the kit does (ADR-0020); any other difference keeps the reading with the ``conflict`` status.
"""

from __future__ import annotations

from kdcheck.config import FileDesignations
from kdcheck.normalize.designations import base_designation, digits, doc_code, one_digit_apart
from kdcheck.schemas import Extracted


def reconcile(read: Extracted, named: str, rules: FileDesignations) -> str | None:
    """Decide ``read``, the designation of a title block, against ``named``, the designation of its file name, both
    normalized by the grammars; change ``read`` in place and return a note for the document, None when they agree.

    A file name without the code of the document («СБ») agrees with a reading that has it.
    """
    value = str(read.value)
    if named in (value, base_designation(value)):
        return None
    differs = f"Обозначение в имени файла ({named}) отличается от основной надписи ({value})"
    if base_designation(named) == base_designation(value):  # only the code of the document differs
        return differs
    read.status = "conflict"
    method = read.source.method if read.source is not None else None
    numbers, wanted = digits(base_designation(value)), digits(base_designation(named))
    misread = numbers == wanted or (rules.one_digit and one_digit_apart(numbers, wanted))
    if method not in rules.methods or len(wanted) < rules.min_digits or not misread:
        return differs
    code = doc_code(named) or doc_code(value)
    read.raw = read.raw or value
    read.value = f"{base_designation(named)} {code}" if code else base_designation(named)
    label = "Основная надпись, «Обозначение документа»"
    return f"{label}: прочитано «{value}», в имени файла «{named}»; в отчёте — «{read.value}»"
