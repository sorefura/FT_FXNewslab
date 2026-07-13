from dataclasses import dataclass
from datetime import datetime

from .ids import ObservationId
from .time import require_utc


@dataclass(frozen=True, slots=True)
class NewsObservation:
    observation_id: ObservationId
    source: str
    title: str
    body: str
    published_at: datetime | None
    first_seen_at: datetime
    content_hash: str
    payload_reference: str
    normalizer_version: str

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.title.strip():
            raise ValueError("NewsObservation source and title must not be blank")
        if self.published_at is not None:
            require_utc(self.published_at, "published_at")
        require_utc(self.first_seen_at, "first_seen_at")
        invalid_hash = len(self.content_hash) != 64 or any(
            c not in "0123456789abcdef" for c in self.content_hash
        )
        if invalid_hash:
            raise ValueError("content_hash must be a lowercase SHA-256 hex digest")
        if not self.payload_reference.strip() or not self.normalizer_version.strip():
            raise ValueError("payload_reference and normalizer_version must not be blank")
