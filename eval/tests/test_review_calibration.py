"""The review mark calibrated: what is marked, and the lowest threshold that keeps the unmarked values right enough."""

import review_calibration as rc


def value(doc: str, confidence: float | None, right: bool, method: str = "ocr", conflict: bool = False) -> rc.Value:
    return rc.Value(
        document=doc, part="основные надписи", method=method, confidence=confidence, conflict=conflict, right=right
    )


def test_a_value_is_marked_by_its_method_its_conflict_or_a_confidence_below_the_threshold():
    assert value("d", 0.99, True, method="filename").marked(0.75)
    assert value("d", 0.99, True, conflict=True).marked(0.75)
    assert value("d", 0.7, True).marked(0.75) and value("d", None, True).marked(0.75)
    assert not value("d", 0.95, True).marked(0.75)


def test_the_threshold_is_the_lowest_that_keeps_the_target_on_the_first_half_and_the_second_checks_it():
    docs = [f"arm/arm-1/{i}.jpg" for i in range(200)]
    values = [value(d, 0.7, i % 2 == 0) for i, d in enumerate(docs)] + [value(d, 0.95, True) for d in docs]
    result = rc.calibrate(values, target=0.02)
    assert result["threshold"] == 0.75  # 0.7 leaves half of the values of 0.7 wrong and unmarked
    assert result["check"]["unmarked_wrong"] == 0 and result["check"]["marked"] == result["check"]["values"] // 2
    assert {v.half for v in values} == {0, 1}
