from decimal import Decimal

import pytest

from app.services.allocations import allocate_by_percentages, allocate_proportionally


def test_percentages_use_deterministic_largest_remainder() -> None:
    result = allocate_by_percentages(
        Decimal("100.00"), [Decimal("33.333333"), Decimal("33.333333"), Decimal("33.333334")]
    )
    assert result == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]
    assert sum(result) == Decimal("100.00")


def test_percentages_must_total_exactly_one_hundred() -> None:
    with pytest.raises(ValueError, match="100"):
        allocate_by_percentages(Decimal("10.00"), [Decimal("50"), Decimal("49.99")])


def test_proportional_split_preserves_every_cent() -> None:
    result = allocate_proportionally(
        Decimal("12.01"), [Decimal("60500"), Decimal("36300"), Decimal("24200")]
    )
    assert result == [Decimal("6.01"), Decimal("3.60"), Decimal("2.40")]
    assert sum(result) == Decimal("12.01")

