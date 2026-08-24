from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

COMBINED_ACCOUNT_RE = re.compile(r"^(?:(\d{1,6})-)?(\d{1,10})/(\d{4})$")
ACCOUNT_RE = re.compile(r"^(?:(\d{1,6})-)?(\d{1,10})$")
BANK_CODE_RE = re.compile(r"^\d{4}$")


@dataclass(frozen=True)
class NormalizedBankAccount:
    raw: str | None
    prefix: str | None
    number: str | None
    bank_code: str | None

    @property
    def account(self) -> str | None:
        if not self.number:
            return None
        return f"{self.prefix}-{self.number}" if self.prefix else self.number

    @property
    def combined(self) -> str | None:
        if not self.account or not self.bank_code:
            return None
        return f"{self.account}/{self.bank_code}"


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def normalize_bank_account(
    bank_account: Any,
    bank_code: Any = None,
) -> NormalizedBankAccount:
    """Normalize Czech domestic account data without guessing missing digits.

    A complete ``[prefix-]number/code`` value wins even when an LLM copied that
    same combined string into both input fields. Invalid input is retained in
    ``raw`` so the original evidence remains reviewable.
    """

    account_input = _compact(bank_account)
    code_input = _compact(bank_code)
    raw = account_input or code_input or None

    for candidate in (account_input, code_input):
        match = COMBINED_ACCOUNT_RE.fullmatch(candidate)
        if match:
            prefix, number, code = match.groups()
            return NormalizedBankAccount(raw=candidate, prefix=prefix, number=number, bank_code=code)

    account_match = ACCOUNT_RE.fullmatch(account_input)
    code = code_input if BANK_CODE_RE.fullmatch(code_input) else None
    if account_match:
        prefix, number = account_match.groups()
        combined_raw = f"{account_input}/{code}" if code else account_input
        return NormalizedBankAccount(
            raw=combined_raw,
            prefix=prefix,
            number=number,
            bank_code=code,
        )

    return NormalizedBankAccount(raw=raw, prefix=None, number=None, bank_code=code)


def normalize_payment_data(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    account = normalize_bank_account(data.get("bank_account"), data.get("bank_code"))
    normalized.update(
        {
            "bank_account_raw": account.raw,
            "bank_account_prefix": account.prefix,
            "bank_account_number": account.number,
            "bank_code": account.bank_code,
            # Backward-compatible field used by older revisions and clients.
            "bank_account": account.account,
        }
    )
    if normalized.get("iban"):
        normalized["iban"] = _compact(normalized["iban"]).upper()
    if normalized.get("swift_bic"):
        normalized["swift_bic"] = _compact(normalized["swift_bic"]).upper()
    return normalized


def valid_czech_account_checksum(prefix: str | None, number: str) -> bool:
    """Return the Czech clearing-system modulo-11 checksum result."""

    if not re.fullmatch(r"\d{1,10}", number):
        return False
    if prefix and not re.fullmatch(r"\d{1,6}", prefix):
        return False
    prefix_digits = (prefix or "").zfill(6)
    number_digits = number.zfill(10)
    prefix_weights = (10, 5, 8, 4, 2, 1)
    number_weights = (6, 3, 7, 9, 10, 5, 8, 4, 2, 1)
    return (
        sum(
            int(digit) * weight
            for digit, weight in zip(prefix_digits, prefix_weights, strict=True)
        )
        % 11
        == 0
        and sum(
            int(digit) * weight
            for digit, weight in zip(number_digits, number_weights, strict=True)
        )
        % 11
        == 0
    )
