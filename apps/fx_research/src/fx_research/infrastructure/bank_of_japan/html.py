from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ...collection import CollectedNewsItem, SourceConfig
from ...detail import OfficialDetailExtractor
from ...errors import SourceRetrievalError, SourceStructureError
from ...http import HttpClient


class BankOfJapanHtmlSource:
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
        soup = BeautifulSoup(response.body, "html.parser")
        root = soup.find("main") or soup.find(id="contents") or soup.find(id="content")
        if root is None:
            raise SourceStructureError(f"{self.source_id} listing content root changed")
        rows = []
        for link in root.select("table a[href]"):
            row = link.find_parent("tr")
            if row is None:
                continue
            cells = [" ".join(cell.stripped_strings) for cell in row.find_all(["th", "td"])]
            date_text = cells[0] if len(cells) >= 2 else None
            title = " ".join(link.stripped_strings)
            if title:
                rows.append((date_text, title, urljoin(response.url, str(link.get("href")))))
        if not rows:
            raise SourceStructureError(f"{self.source_id} listing contains no recognized items")
        return tuple(self._item(*row) for row in rows[: self._config.limit])

    def _item(self, date_text: str | None, title: str, url: str) -> CollectedNewsItem:
        detail = self._http.get(url)
        if detail.status_code != 200:
            raise SourceRetrievalError(
                f"{self.source_id} detail returned HTTP {detail.status_code}"
            )
        return CollectedNewsItem(
            source_id=self.source_id,
            candidate_currency=self._config.candidate_currency,
            canonical_url=url,
            title=title,
            body=self._detail_extractor.extract(detail),
            published_at=None,
            source_date_text=date_text,
            normalizer_version=self._config.normalizer_version,
        )
