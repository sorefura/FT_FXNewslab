from dataclasses import dataclass


def _optional_version(value: str | None, label: str) -> None:
    if value is not None and not value.strip():
        raise ValueError(f"{label} must not be blank")


@dataclass(frozen=True, slots=True)
class VersionMetadata:
    producer_version: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None
    scorer_version: str | None = None
    transformation_version: str | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("producer_version", self.producer_version),
            ("model_version", self.model_version),
            ("prompt_version", self.prompt_version),
            ("scorer_version", self.scorer_version),
            ("transformation_version", self.transformation_version),
        ):
            _optional_version(value, label)

    def require_feature_versions(self) -> None:
        if not self.producer_version or not self.model_version or not self.prompt_version:
            raise ValueError("Feature requires producer, model, and prompt versions")

    def require_signal_versions(self) -> None:
        if not self.scorer_version:
            raise ValueError("Signal requires a scorer version")

