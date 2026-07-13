from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class _Identifier:
    value: str

    def __post_init__(self) -> None:
        if not self.value or not self.value.strip():
            raise ValueError(f"{type(self).__name__} must not be empty")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ObservationId(_Identifier):
    pass


@dataclass(frozen=True, slots=True)
class FeatureId(_Identifier):
    pass


@dataclass(frozen=True, slots=True)
class SignalId(_Identifier):
    pass

