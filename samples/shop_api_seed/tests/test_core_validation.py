import pytest

from app.core.validation import require_positive, validate_email, validate_quantity


def test_require_positive():
    assert require_positive(1.5, "amount") == 1.5


def test_require_positive_rejects():
    with pytest.raises(ValueError):
        require_positive(0, "amount")


def test_validate_quantity():
    assert validate_quantity(12) == 12
    with pytest.raises(ValueError):
        validate_quantity(0)
    with pytest.raises(TypeError):
        validate_quantity("2")  # type: ignore[arg-type]


def test_validate_email():
    assert validate_email(" Ada@Example.COM ") == "ada@example.com"
    with pytest.raises(ValueError):
        validate_email("not-an-email")