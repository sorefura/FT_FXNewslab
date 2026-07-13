from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class Currency:
    code: str

    def __post_init__(self) -> None:
        if len(self.code) != 3 or not self.code.isascii() or not self.code.isalpha():
            raise ValueError("Currency must be a three-letter ASCII code")
        if self.code != self.code.upper():
            raise ValueError("Currency must be uppercase")

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class CurrencyPair:
    base: Currency
    quote: Currency

    def __post_init__(self) -> None:
        if self.base == self.quote:
            raise ValueError("CurrencyPair base and quote must differ")

    @classmethod
    def parse(cls, value: str) -> "CurrencyPair":
        separator = "_" if "_" in value else "/" if "/" in value else None
        if separator is None:
            raise ValueError("CurrencyPair must use BASE_QUOTE or BASE/QUOTE")
        parts = value.split(separator)
        if len(parts) != 2:
            raise ValueError("CurrencyPair must contain exactly two currencies")
        return cls(Currency(parts[0]), Currency(parts[1]))

    @property
    def symbol(self) -> str:
        return f"{self.base.code}_{self.quote.code}"

