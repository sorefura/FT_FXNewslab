from datetime import UTC, datetime
from pathlib import Path

import pytest
from fx_core import Currency
from fx_research.collection import SourceConfig
from fx_research.errors import SourceStructureError
from fx_research.http import HttpResponse
from fx_research.infrastructure.bank_of_japan import BankOfJapanHtmlSource
from fx_research.infrastructure.federal_reserve import FederalReserveRssSource

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures"


class RecordedHttpClient:
    def __init__(self, responses: dict[str, HttpResponse]) -> None:
        self._responses = responses

    def get(self, url: str) -> HttpResponse:
        return self._responses[url]


def _response(url: str, fixture: str, content_type: str) -> HttpResponse:
    return HttpResponse(
        url=url,
        status_code=200,
        headers={"content-type": content_type},
        body=(FIXTURES / fixture).read_bytes(),
    )


@pytest.mark.parametrize(
    ("source_id", "fixture", "published_at"),
    [
        (
            "fed.press_monetary.rss",
            "fed_press_monetary.xml",
            datetime(2026, 7, 13, 18, 0, tzinfo=UTC),
        ),
        (
            "fed.speeches.rss",
            "fed_speeches.xml",
            datetime(2026, 7, 12, 14, 30, tzinfo=UTC),
        ),
    ],
)
def test_federal_reserve_rss_items_are_separate_usd_sources(
    source_id: str, fixture: str, published_at: datetime
) -> None:
    listing_url = f"https://fixture.test/{fixture}"
    source = FederalReserveRssSource(
        SourceConfig(source_id, Currency("USD"), listing_url, "fed-rss-v1"),
        RecordedHttpClient({listing_url: _response(listing_url, fixture, "application/rss+xml")}),
    )

    item = source.fetch()[0]

    assert item.source_id == source_id
    assert item.candidate_currency == Currency("USD")
    assert item.published_at == published_at


def test_malformed_federal_reserve_rss_creates_no_item() -> None:
    listing_url = "https://fixture.test/malformed.xml"
    source = FederalReserveRssSource(
        SourceConfig("fed.press_monetary.rss", Currency("USD"), listing_url, "fed-rss-v1"),
        RecordedHttpClient(
            {listing_url: HttpResponse(listing_url, 200, {}, b"<rss><channel>")}
        ),
    )

    with pytest.raises(SourceStructureError, match="malformed or empty RSS"):
        source.fetch()


@pytest.mark.parametrize(
    ("source_id", "listing_fixture", "detail_url"),
    [
        (
            "boj.monetary_policy.html",
            "boj_monetary_listing.html",
            "https://www.boj.or.jp/en/mopo/mpmdeci/mpr_2026/example.htm",
        ),
        (
            "boj.speeches.html",
            "boj_speeches_listing.html",
            "https://www.boj.or.jp/en/about/press/koen_2026/example.htm",
        ),
    ],
)
def test_bank_of_japan_html_items_are_separate_jpy_sources_without_fabricated_time(
    source_id: str, listing_fixture: str, detail_url: str
) -> None:
    listing_url = f"https://www.boj.or.jp/{listing_fixture}"
    source = BankOfJapanHtmlSource(
        SourceConfig(source_id, Currency("JPY"), listing_url, "boj-html-v1"),
        RecordedHttpClient(
            {
                listing_url: _response(listing_url, listing_fixture, "text/html"),
                detail_url: _response(detail_url, "boj_detail.html", "text/html"),
            }
        ),
    )

    item = source.fetch()[0]

    assert item.source_id == source_id
    assert item.candidate_currency == Currency("JPY")
    assert item.published_at is None
    assert item.source_date_text is not None
    assert "Site-wide" not in item.body


def test_changed_bank_of_japan_html_structure_fails_explicitly() -> None:
    listing_url = "https://www.boj.or.jp/changed.htm"
    source = BankOfJapanHtmlSource(
        SourceConfig("boj.monetary_policy.html", Currency("JPY"), listing_url, "boj-html-v1"),
        RecordedHttpClient(
            {listing_url: _response(listing_url, "boj_changed_structure.html", "text/html")}
        ),
    )

    with pytest.raises(SourceStructureError, match="content root changed"):
        source.fetch()
