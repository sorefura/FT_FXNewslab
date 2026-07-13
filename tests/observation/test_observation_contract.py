from datetime import datetime

import pytest
from fx_core import NewsObservation

from tests.factories import observation


def test_observation_distinguishes_source_time_from_first_availability() -> None:
    created = observation()
    assert created.published_at is not None
    assert created.first_seen_at == created.published_at


def test_observation_rejects_naive_first_seen_at() -> None:
    created = observation()
    with pytest.raises(ValueError):
        NewsObservation(
            observation_id=created.observation_id,
            source=created.source,
            title=created.title,
            body=created.body,
            published_at=created.published_at,
            first_seen_at=datetime(2026, 7, 13),
            content_hash=created.content_hash,
            payload_reference=created.payload_reference,
            normalizer_version=created.normalizer_version,
        )

