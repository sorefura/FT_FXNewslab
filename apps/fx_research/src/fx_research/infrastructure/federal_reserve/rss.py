from datetime import UTC, datetime
from time import struct_time
from typing import Any

import feedparser
from bs4 import BeautifulSoup

from ...collection import CollectedNewsItem, SourceConfig
from ...detail import OfficialDetailExtractor
from ...errors import SourceRetrievalError, SourceStructureError
from ...http import HttpClient


class FederalReserveRssSource:
    def __init__(
        self,
        config: SourceConfig,
        http: HttpClient,
        detail_extractor: OfficialDetailExtractor | None = None,
    ) -> None:
        self._config = config
        self._http = http
        self._detail_extractor = detail_extractor or OfficialDetailExtractor()

    @property
    def source_id(self) -> str:
        return self._config.source_id

    def fetch(self) -> tuple[CollectedNewsItem, ...]:
        response = self._http.get(self._config.listing_url)
        if response.status_code != 200:
            raise SourceRetrievalError(
                f"{self.source_id} returned HTTP {response.status_code}"
            )
        parsed = feedparser.parse(response.body)
        if parsed.bozo or not parsed.entries:
            raise SourceStructureError(f"{self.source_id} returned malformed or empty RSS")
        return tuple(self._item(entry) for entry in parsed.entries[: self._config.limit])

    def _item(self, entry: Any) -> CollectedNewsItem:
        title = str(entry.get("title", "")).strip()
        url = str(entry.get("link", "")).strip()
        if not title or not url:
            raise SourceStructureError(f"{self.source_id} RSS item lacks title or link")
        summary = BeautifulSoup(str(entry.get("summary", "")), "html.parser").get_text(" ")
        body = " ".join(summary.split())
        if not body:
            detail = self._http.get(url)
            if detail.status_code != 200:
                raise SourceRetrievalError(
                    f"{self.source_id} detail returned HTTP {detail.status_code}"
                )
            body = self._detail_extractor.extract(detail)
        source_date = str(entry.get("published", "")).strip() or None
        return CollectedNewsItem(
            source_id=self.source_id,
            candidate_currency=self._config.candidate_currency,
            canonical_url=url,
            title=title,
            body=body,
            published_at=self._published_at(entry.get("published_parsed")),
            source_date_text=source_date,
            normalizer_version=self._config.normalizer_version,
        )

    @staticmethod
    def _published_at(value: struct_time | None) -> datetime | None:
        if value is None:
            return None
        return datetime(*value[:6], tzinfo=UTC)
