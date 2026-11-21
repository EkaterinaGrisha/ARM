"""Status of the extracted values (contract 1.2): which ones the technologist should check against the document.

``review`` in ``configs/ingest.yaml`` sets the rule: a value read by one of its methods, or with a lower confidence,
gets ``low_confidence``; in a run the model did not read, the methods of ``without_model`` too. A value without a
value is ``missing``. ``conflict`` comes only from an extractor that compared two sources; this module keeps a status
set before it.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import BaseModel

from kdcheck.config import Review
from kdcheck.schemas import Extracted, ExtractedStatus


def status(ex: Extracted, review: Review, *, model: bool = False) -> ExtractedStatus:
    if ex.value in (None, ""):
        return "missing"
    source = ex.source
    method = source.method if source else None
    confidence = ex.confidence if ex.confidence is not None else (source.confidence if source else None)
    methods = review.methods if model else (*review.methods, *review.without_model)
    if method in methods or (confidence is not None and confidence < review.min_confidence):
        return "low_confidence"
    return "ok"


def values(model: BaseModel) -> Iterator[Extracted]:
    """Every extracted value inside ``model``, at any depth."""
    for name in type(model).model_fields:
        field = getattr(model, name)
        for item in field if isinstance(field, list) else [field]:
            if isinstance(item, Extracted):
                yield item
            elif isinstance(item, BaseModel):
                yield from values(item)


def mark(model: BaseModel, review: Review, *, read_by_model: bool = False) -> None:
    """Give every value inside ``model`` that has no status yet its status by ``review``."""
    for ex in values(model):
        if ex.status is None:
            ex.status = status(ex, review, model=read_by_model)
