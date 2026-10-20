"""Upload paths from the browser are sanitized before they touch the disk."""

import pytest
from arm_web.jobs import safe_relative_path


@pytest.mark.parametrize(
    "name, expected",
    [
        ("kit/Чертежи/a.pdf", "kit/Чертежи/a.pdf"),
        ("../../etc/passwd", "etc/passwd"),
        ("C:\\Users\\x\\a.pdf", "Users/x/a.pdf"),
        ("/abs/path.pdf", "abs/path.pdf"),
        ("..", "file"),
        ("bad:name?.pdf", "bad_name_.pdf"),
    ],
)
def test_safe_relative_path(name, expected):
    assert safe_relative_path(name).as_posix() == expected
