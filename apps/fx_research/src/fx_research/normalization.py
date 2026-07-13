import hashlib
import unicodedata
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fx_core import NewsObservation, ObservationId

from .collection import CollectedNewsItem


class NewsNormalizer:
    def observation_id(self, item: CollectedNewsItem) -> ObservationId:
        title = self.normalize_text(item.title)
        body = self.normalize_text(item.body)
        content_hash = self.content_hash(title, body)
        identity = "\0".join(
            (item.source_id, self.canonical_url(item.canonical_url), content_hash)
        ).encode()
        return ObservationId(f"obs-{hashlib.sha256(identity).hexdigest()}")

    def normalize(
        self, item: CollectedNewsItem, *, first_seen_at: datetime
    ) -> NewsObservation:
        title = self.normalize_text(item.title)
        body = self.normalize_text(item.body)
        return NewsObservation(
            observation_id=self.observation_id(item),
            source=item.source_id,
            title=title,
            body=body,
            published_at=item.published_at,
            first_seen_at=first_seen_at,
            content_hash=self.content_hash(title, body),
            payload_reference=self.canonical_url(item.canonical_url),
            normalizer_version=item.normalizer_version,
        )

    @staticmethod
    def normalize_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value)
        return " ".join(normalized.split())

    @staticmethod
    def content_hash(title: str, body: str) -> str:
        return hashlib.sha256(f"{title}\n{body}".encode()).hexdigest()

    @staticmethod
    def canonical_url(value: str) -> str:
        parsed = urlsplit(value.strip())
        query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
        path = parsed.path or "/"
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, query, ""))
