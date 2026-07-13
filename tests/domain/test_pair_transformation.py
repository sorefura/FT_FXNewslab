from fx_core import (
    Currency,
    CurrencyPair,
    CurrencyPairSignalTransformer,
    CurrencyTarget,
    DirectionScore,
    PairScore,
    SignalId,
)

from tests.factories import NOW, signal


def test_pair_score_subtracts_quote_currency_from_base_currency() -> None:
    usd = signal("usd-signal", "usd-feature")
    jpy = signal("jpy-signal", "jpy-feature")
    jpy = type(jpy)(
        signal_id=jpy.signal_id,
        target=CurrencyTarget(Currency("JPY")),
        signal_type=jpy.signal_type,
        direction=DirectionScore(-0.2),
        strength=jpy.strength,
        confidence=jpy.confidence,
        horizon=jpy.horizon,
        observed_at=jpy.observed_at,
        created_at=jpy.created_at,
        source_feature_ids=jpy.source_feature_ids,
        versions=jpy.versions,
    )
    result = CurrencyPairSignalTransformer().transform(
        usd,
        jpy,
        pair=CurrencyPair.parse("USD_JPY"),
        signal_id=SignalId("pair-signal"),
        created_at=NOW,
    )
    assert isinstance(result.direction, PairScore)
    assert result.direction.value == 0.8
    assert result.versions.transformation_version == "currency-pair-v1"

