from fx_core import FundamentalSignalScorer, Horizon, SignalId

from tests.factories import NOW, feature


def test_fundamental_scorer_preserves_feature_lineage_and_versions() -> None:
    source = feature()
    result = FundamentalSignalScorer().score(
        source,
        signal_id=SignalId("signal-1"),
        observed_at=NOW,
        created_at=NOW,
    )
    assert result.source_feature_ids == (source.feature_id,)
    assert result.versions.model_version == "model-v1"
    assert result.versions.prompt_version == "prompt-v1"
    assert result.versions.scorer_version == "fundamental-scorer-v1"
    assert result.horizon is Horizon.DAYS_3

