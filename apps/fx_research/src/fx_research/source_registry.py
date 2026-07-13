from dataclasses import replace
from datetime import UTC, datetime

from fx_core import Currency

from .collection import NewsSource, SourceConfig
from .http import HttpClient
from .infrastructure.bank_of_japan import BankOfJapanHtmlSource
from .infrastructure.federal_reserve import FederalReserveRssSource


def source_configs(year: int | None = None) -> dict[str, SourceConfig]:
    current_year = year or datetime.now(UTC).year
    return {
        "fed.press_monetary.rss": SourceConfig(
            "fed.press_monetary.rss",
            Currency("USD"),
            "https://www.federalreserve.gov/feeds/press_monetary.xml",
            "fed-rss-v1",
        ),
        "fed.speeches.rss": SourceConfig(
            "fed.speeches.rss",
            Currency("USD"),
            "https://www.federalreserve.gov/feeds/speeches.xml",
            "fed-rss-v1",
        ),
        "boj.monetary_policy.html": SourceConfig(
            "boj.monetary_policy.html",
            Currency("JPY"),
            f"https://www.boj.or.jp/en/mopo/mpmdeci/mpr_{current_year}/index.htm",
            "boj-html-v1",
        ),
        "boj.speeches.html": SourceConfig(
            "boj.speeches.html",
            Currency("JPY"),
            "https://www.boj.or.jp/en/mopo/r_menu_koen/index.htm",
            "boj-html-v1",
        ),
    }


def build_source(source_id: str, http: HttpClient, *, limit: int = 20) -> NewsSource:
    try:
        config = replace(source_configs()[source_id], limit=limit)
    except KeyError as error:
        raise ValueError(f"Unknown source: {source_id}") from error
    if source_id.startswith("fed."):
        return FederalReserveRssSource(config, http)
    return BankOfJapanHtmlSource(config, http)
