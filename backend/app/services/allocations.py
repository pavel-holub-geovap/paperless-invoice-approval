from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

CENT = Decimal("0.01")


def allocate_by_percentages(total: Decimal, percentages: list[Decimal]) -> list[Decimal]:
    if not percentages:
        raise ValueError("At least one percentage is required")
    if any(p < 0 for p in percentages):
        raise ValueError("Percentages cannot be negative")
    if sum(percentages, Decimal("0")) != Decimal("100"):
        raise ValueError("Percentages must add up to exactly 100")

    exact = [total * p / Decimal("100") for p in percentages]
    rounded_down = [value.quantize(CENT, rounding=ROUND_DOWN) for value in exact]
    missing_cents = int(((total - sum(rounded_down, Decimal("0"))) / CENT).to_integral_value())
    remainders = sorted(
        range(len(exact)), key=lambda index: (exact[index] - rounded_down[index], -index), reverse=True
    )
    result = rounded_down[:]
    for index in remainders[:missing_cents]:
        result[index] += CENT
    if sum(result, Decimal("0")) != total.quantize(CENT):
        raise AssertionError("Deterministic allocation rounding failed")
    return result


def allocate_proportionally(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    if not weights or any(weight < 0 for weight in weights):
        raise ValueError("Non-negative weights are required")
    weight_sum = sum(weights, Decimal("0"))
    if weight_sum <= 0:
        raise ValueError("Weight sum must be positive")
    exact = [total * weight / weight_sum for weight in weights]
    rounded_down = [value.quantize(CENT, rounding=ROUND_DOWN) for value in exact]
    missing_cents = int(((total.quantize(CENT) - sum(rounded_down, Decimal("0"))) / CENT).to_integral_value())
    order = sorted(
        range(len(exact)), key=lambda index: (exact[index] - rounded_down[index], -index), reverse=True
    )
    result = rounded_down[:]
    for index in order[:missing_cents]:
        result[index] += CENT
    return result
