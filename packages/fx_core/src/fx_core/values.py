from dataclasses import dataclass
from math import isfinite


def _bounded(value: float, lower: float, upper: float, label: str) -> None:
    if not isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"{label} must be between {lower} and {upper}")


@dataclass(frozen=True, slots=True)
class DirectionScore:
    value: float

    def __post_init__(self) -> None:
        _bounded(self.value, -1.0, 1.0, "DirectionScore")


@dataclass(frozen=True, slots=True)
class PairScore:
    value: float

    def __post_init__(self) -> None:
        _bounded(self.value, -2.0, 2.0, "PairScore")


@dataclass(frozen=True, slots=True)
class Probability:
    value: float

    def __post_init__(self) -> None:
        _bounded(self.value, 0.0, 1.0, "Probability")

