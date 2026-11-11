"""Limit deviations of tolerance fields against the tables of GOST 25347 (ISO 286-2)."""

import pytest
from kdcheck.config import load_settings
from kdcheck.params.tolerances import deviations

TABLE = load_settings(use_ocr=False).tolerances


@pytest.mark.parametrize(
    ("field", "size", "lower", "upper"),
    [
        ("H7", 40, 0, 25),
        ("H8", 40, 0, 39),
        ("h6", 40, -16, 0),
        ("f7", 40, -50, -25),
        ("g6", 40, -25, -9),
        ("e8", 40, -89, -50),
        ("d9", 40, -142, -80),
        ("k6", 40, 2, 18),
        ("m6", 40, 9, 25),
        ("n6", 40, 17, 33),
        ("p6", 40, 26, 42),
        ("js6", 40, -8, 8),
        ("js7", 40, -12, 12),  # IT7 = 25: an odd IT of js7–js11 goes to the even number below
        ("F8", 40, 25, 64),
        ("G7", 40, 9, 34),
        ("K6", 40, -13, 3),
        ("K7", 40, -18, 7),
        ("K8", 40, -27, 12),
        ("M7", 40, -25, 0),
        ("M8", 40, -34, 5),
        ("N7", 40, -33, -8),
        ("N9", 40, -62, 0),
        ("P6", 40, -37, -21),
        ("P7", 40, -42, -17),
        ("JS7", 40, -12, 12),
        ("Js9", 16, -21, 21),  # IT9 = 43 at 10–18 mm: odd, so ±21
        ("k6", 25, 2, 15),
        ("m6", 100, 13, 35),
        ("p6", 200, 50, 79),
        ("H7", 10, 0, 15),  # 10 mm is the top of the range over 6 up to 10
        ("N7", 3, -14, -4),  # up to 3 mm Δ = 0
        ("M7", 2, -12, -2),
        ("k8", 40, 0, 39),  # k outside IT4–IT7 has ei = 0
    ],
)
def test_deviations_agree_with_the_tables_of_gost_25347(field, size, lower, upper):
    assert deviations(field, size, TABLE) == (lower / 1000, upper / 1000)


@pytest.mark.parametrize(("field", "size"), [("H7", 600), ("r6", 40), ("K9", 40), ("H19", 40), ("7H", 40), ("H7", 0)])
def test_a_field_or_size_the_tables_do_not_hold_gets_no_deviations(field, size):
    assert deviations(field, size, TABLE) is None
