from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from fx_core import Currency
from fx_core.time import require_utc


@dataclass(frozen=True, slots=True)
class SourceConfig:
    source_id: str
    candidate_currency: Currency
    listing_url: str
    normalizer_version: str
    limit: int = 20

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.listing_url.startswith("https://"):
            raise ValueError("Source identity and HTTPS listing URL are required")
        if not self.normalizer_version.strip() or self.limit < 1:
            raise ValueError("Source normalizer version and positive limit are required")


@dataclass(frozen=True, slots=True)
class CollectedNewsItem:
    source_id: str
    candidate_currency: Currency
    canonical_url: str
    title: str
    body: str
    published_at: datetime | None
    source_date_text: str | None
    normalizer_version: str

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.canonical_url.startswith("https://"):
            raise ValueError("Collected item requires source identity and canonical HTTPS URL")
        if not self.title.strip() or not self.body.strip():
            raise ValueError("Collected item requires analyzable title and body")
        if self.published_at is not None:
            require_utc(self.published_at, "collected_item.published_at")
        if not self.normalizer_version.strip():
            raise ValueError("Collected item requires normalizer version")


class NewsSource(Protocol):
    @property
    def source_id(self) -> str: ...

    def fetch(self) -> tuple[CollectedNewsItem, ...]: ...
