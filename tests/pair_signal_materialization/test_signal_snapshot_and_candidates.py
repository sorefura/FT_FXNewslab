from dataclasses import replace
from datetime import timedelta

import pytest
from fx_core import (
    Currency,
    CurrencyPair,
    FeatureId,
    Horizon,
    ObservationId,
    PairScore,
    PairTarget,
    Probability,
    Signal,
    SignalId,
    VersionMetadata,
)
from fx_signal_store import (
    PairSignalCandidateEligibility,
    PairSignalCandidateRejectionReason,
    SignalContentSnapshot,
    SignalLineage,
    SourceSignalRole,
    canonical_observation_ids,
    inspect_source_candidate,
    observation_group_identity,
)

from tests.pair_signal_materialization.factories import (
    NOW,
    PAIR,
    request,
    source_signal,
    source_snapshot,
)


def test_observation_group_uses_exact_canonical_id_set() -> None:
    first = (ObservationId("observation-z"), ObservationId("observation-a"))
    second = tuple(reversed(first))

    assert canonical_observation_ids(first) == (
        ObservationId("observation-a"),
        ObservationId("observation-z"),
    )
    assert observation_group_identity(first) == observation_group_identity(second)
    assert observation_group_identity(first) != observation_group_identity(first[:1])


def test_observation_group_rejects_empty_duplicate_and_untyped_ids() -> None:
    with pytest.raises(ValueError, match="empty"):
        observation_group_identity(())
    with pytest.raises(ValueError, match="unique"):
        observation_group_identity((ObservationId("same"), ObservationId("same")))
    with pytest.raises(TypeError, match="ObservationId"):
        observation_group_identity(("observation-1",))  # type: ignore[arg-type]


def test_signal_snapshot_canonicalizes_set_like_feature_and_observation_lineage() -> None:
    signal = source_signal(
        feature_ids=(FeatureId("feature-z"), FeatureId("feature-a"))
    )
    first = SignalContentSnapshot.from_signal(
        signal,
        SignalLineage(
            signal_id=signal.signal_id,
            feature_ids=(FeatureId("feature-a"), FeatureId("feature-z")),
            observation_ids=(ObservationId("observation-z"), ObservationId("observation-a")),
        ),
    )
    second = SignalContentSnapshot.from_signal(
        signal,
        SignalLineage(
            signal_id=signal.signal_id,
            feature_ids=(FeatureId("feature-z"), FeatureId("feature-a")),
            observation_ids=(ObservationId("observation-a"), ObservationId("observation-z")),
        ),
    )

    assert first == second
    assert first.source_feature_ids == (FeatureId("feature-a"), FeatureId("feature-z"))
    assert first.source_observation_ids == (
        ObservationId("observation-a"),
        ObservationId("observation-z"),
    )


@pytest.mark.parametrize(
    "signal_change",
    [
        {"identifier": "signal-other"},
        {"target_currency": Currency("EUR")},
        {"direction": -0.4},
        {"strength": 0.4},
        {"confidence": 0.4},
        {"horizon": Horizon.DAY_1},
        {"producer_version": "producer-v2"},
        {"model_version": "model-v2"},
        {"prompt_version": "prompt-v2"},
        {"scorer_version": "scorer-v2"},
        {"transformation_version": "source-transform-v1"},
    ],
)
def test_signal_content_hash_changes_with_signal_content_or_version(
    signal_change: dict[str, object],
) -> None:
    assert source_snapshot().signal_content_hash != source_snapshot(
        **signal_change
    ).signal_content_hash


def test_signal_content_hash_changes_with_feature_or_observation_lineage() -> None:
    assert source_snapshot().signal_content_hash != source_snapshot(
        signal=source_signal(feature_ids=(FeatureId("feature-other"),))
    ).signal_content_hash
    assert source_snapshot().signal_content_hash != source_snapshot(
        observation_ids=(ObservationId("observation-other"),)
    ).signal_content_hash


def test_signal_snapshot_rejects_mismatched_or_corrupt_lineage_and_hash() -> None:
    signal = source_signal()
    with pytest.raises(ValueError, match="another Signal"):
        SignalContentSnapshot.from_signal(
            signal,
            SignalLineage(
                signal_id=SignalId("signal-other"),
                feature_ids=signal.source_feature_ids,
                observation_ids=(ObservationId("observation-1"),),
            ),
        )
    with pytest.raises(ValueError, match="does not match"):
        SignalContentSnapshot.from_signal(
            signal,
            SignalLineage(
                signal_id=signal.signal_id,
                feature_ids=(FeatureId("feature-other"),),
                observation_ids=(ObservationId("observation-1"),),
            ),
        )
    with pytest.raises(ValueError, match="unique"):
        SignalContentSnapshot.from_signal(
            signal,
            SignalLineage(
                signal_id=signal.signal_id,
                feature_ids=signal.source_feature_ids,
                observation_ids=(ObservationId("same"), ObservationId("same")),
            ),
        )
    duplicate_feature_signal = source_signal(
        feature_ids=(FeatureId("same"), FeatureId("same"))
    )
    with pytest.raises(ValueError, match="unique"):
        SignalContentSnapshot.from_signal(
            duplicate_feature_signal,
            SignalLineage(
                signal_id=duplicate_feature_signal.signal_id,
                feature_ids=duplicate_feature_signal.source_feature_ids,
                observation_ids=(ObservationId("observation-1"),),
            ),
        )
    with pytest.raises(ValueError, match="empty"):
        SignalContentSnapshot.from_signal(
            signal,
            SignalLineage(
                signal_id=signal.signal_id,
                feature_ids=signal.source_feature_ids,
                observation_ids=(),
            ),
        )
    with pytest.raises(ValueError, match="signal_content_hash"):
        replace(source_snapshot(), signal_content_hash="signal-content-forged")


def test_signal_snapshot_rejects_currency_pair_direction_mismatch() -> None:
    source = source_snapshot()
    with pytest.raises(TypeError, match="Currency Signal snapshot"):
        replace(source, direction_type=source.direction_type.PAIR_SCORE)


def test_pair_signal_snapshot_preserves_pair_direction_type() -> None:
    pair_signal = Signal(
        signal_id=SignalId("pair-signal-1"),
        target=PairTarget(PAIR),
        signal_type="pair_fundamental",
        direction=PairScore(0.9),
        strength=Probability(0.8),
        confidence=Probability(0.9),
        horizon=Horizon.DAYS_3,
        observed_at=NOW - timedelta(minutes=1),
        created_at=NOW,
        source_feature_ids=(FeatureId("feature-base"), FeatureId("feature-quote")),
        versions=VersionMetadata(
            producer_version="producer-v1",
            model_version="model-v1",
            prompt_version="prompt-v1",
            scorer_version="fundamental-scorer-v1",
            transformation_version="currency-pair-v1",
        ),
    )
    snapshot = SignalContentSnapshot.from_signal(
        pair_signal,
        SignalLineage(
            signal_id=pair_signal.signal_id,
            feature_ids=tuple(reversed(pair_signal.source_feature_ids)),
            observation_ids=(ObservationId("observation-1"),),
        ),
    )

    assert snapshot.target_value == "USD_JPY"
    assert snapshot.direction_value == 0.9
    with pytest.raises(TypeError, match="Pair Signal snapshot requires PairScore"):
        replace(snapshot, direction_type=snapshot.direction_type.DIRECTION_SCORE)


@pytest.mark.parametrize(
    ("signal_change", "expected_reason"),
    [
        (
            {"target_currency": Currency("EUR")},
            PairSignalCandidateRejectionReason.TARGET_CURRENCY_MISMATCH,
        ),
        (
            {"signal_type": "other"},
            PairSignalCandidateRejectionReason.SIGNAL_TYPE_MISMATCH,
        ),
        ({"horizon": Horizon.DAY_1}, PairSignalCandidateRejectionReason.HORIZON_MISMATCH),
        (
            {"producer_version": "producer-v2"},
            PairSignalCandidateRejectionReason.PRODUCER_VERSION_MISMATCH,
        ),
        (
            {"model_version": "model-v2"},
            PairSignalCandidateRejectionReason.MODEL_VERSION_MISMATCH,
        ),
        (
            {"prompt_version": "prompt-v2"},
            PairSignalCandidateRejectionReason.PROMPT_VERSION_MISMATCH,
        ),
        (
            {"scorer_version": "scorer-v2"},
            PairSignalCandidateRejectionReason.SCORER_VERSION_MISMATCH,
        ),
        (
            {"transformation_version": "source-v1"},
            PairSignalCandidateRejectionReason.SOURCE_TRANSFORMATION_VERSION_MISMATCH,
        ),
        (
            {
                "observed_at": NOW + timedelta(seconds=1),
                "created_at": NOW + timedelta(seconds=1),
            },
            PairSignalCandidateRejectionReason.OBSERVED_AFTER_AS_OF,
        ),
        (
            {
                "observed_at": NOW - timedelta(seconds=1),
                "created_at": NOW + timedelta(seconds=1),
            },
            PairSignalCandidateRejectionReason.CREATED_AFTER_AS_OF,
        ),
        (
            {
                "observed_at": NOW - timedelta(hours=5),
                "created_at": NOW - timedelta(hours=4, minutes=59),
            },
            PairSignalCandidateRejectionReason.STALE_AT_AS_OF,
        ),
    ],
)
def test_candidate_inspection_returns_structured_dominant_reason(
    signal_change: dict[str, object],
    expected_reason: PairSignalCandidateRejectionReason,
) -> None:
    inspected = inspect_source_candidate(
        request(),
        SourceSignalRole.BASE,
        source_snapshot(**signal_change),
        1,
    )

    assert inspected.eligibility is PairSignalCandidateEligibility.INELIGIBLE
    assert inspected.rejection_reason is expected_reason


def test_candidate_target_type_mismatch_dominates_other_mismatches() -> None:
    pair_signal = Signal(
        signal_id=SignalId("source-pair"),
        target=PairTarget(CurrencyPair.parse("EUR_USD")),
        signal_type="wrong",
        direction=PairScore(0.2),
        strength=Probability(0.8),
        confidence=Probability(0.9),
        horizon=Horizon.DAY_1,
        observed_at=NOW - timedelta(days=2),
        created_at=NOW - timedelta(days=2),
        source_feature_ids=(FeatureId("feature-pair"),),
        versions=VersionMetadata(scorer_version="other", transformation_version="pair-v2"),
    )
    snapshot = SignalContentSnapshot.from_signal(
        pair_signal,
        SignalLineage(
            signal_id=pair_signal.signal_id,
            feature_ids=pair_signal.source_feature_ids,
            observation_ids=(ObservationId("observation-1"),),
        ),
    )

    inspected = inspect_source_candidate(request(), SourceSignalRole.BASE, snapshot, 1)

    assert inspected.rejection_reason is (
        PairSignalCandidateRejectionReason.TARGET_TYPE_MISMATCH
    )


def test_direction_mismatch_is_intrinsic_and_not_a_candidate_rejection_reason() -> None:
    assert "DIRECTION_TYPE_MISMATCH" not in PairSignalCandidateRejectionReason.__members__
    source = source_snapshot()
    with pytest.raises(TypeError, match="Currency Signal snapshot"):
        replace(source, direction_type=source.direction_type.PAIR_SCORE)


def test_exact_source_candidate_is_eligible_and_group_is_derived() -> None:
    snapshot = source_snapshot(
        observation_ids=(ObservationId("observation-z"), ObservationId("observation-a"))
    )
    inspected = inspect_source_candidate(request(), SourceSignalRole.BASE, snapshot, 3)

    assert inspected.eligibility is PairSignalCandidateEligibility.ELIGIBLE
    assert inspected.rejection_reason is None
    assert inspected.observation_ids == (
        ObservationId("observation-a"),
        ObservationId("observation-z"),
    )
    assert inspected.observation_group_identity == observation_group_identity(
        snapshot.source_observation_ids
    )


def test_candidate_identity_commits_to_role_content_and_store_sequence() -> None:
    materialization_request = request()
    base_snapshot = source_snapshot(SourceSignalRole.BASE)
    first = inspect_source_candidate(
        materialization_request, SourceSignalRole.BASE, base_snapshot, 1
    )
    same = inspect_source_candidate(
        materialization_request, SourceSignalRole.BASE, base_snapshot, 1
    )
    other_role_snapshot = source_snapshot(
        SourceSignalRole.QUOTE,
        signal=source_signal(
            SourceSignalRole.QUOTE,
            identifier=base_snapshot.signal_id.value,
            target_currency=PAIR.quote,
        ),
    )
    other_role = inspect_source_candidate(
        materialization_request, SourceSignalRole.QUOTE, other_role_snapshot, 1
    )
    other_sequence = inspect_source_candidate(
        materialization_request, SourceSignalRole.BASE, base_snapshot, 2
    )
    other_content = inspect_source_candidate(
        materialization_request,
        SourceSignalRole.BASE,
        source_snapshot(SourceSignalRole.BASE, direction=0.6),
        1,
    )

    assert first.candidate_id == same.candidate_id
    assert first.candidate_id != other_role.candidate_id
    assert first.candidate_id != other_sequence.candidate_id
    assert first.candidate_id != other_content.candidate_id


def test_candidate_rejects_bad_sequence_forged_id_and_forged_eligibility() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        inspect_source_candidate(request(), SourceSignalRole.BASE, source_snapshot(), 0)
    eligible = inspect_source_candidate(request(), SourceSignalRole.BASE, source_snapshot(), 1)
    with pytest.raises(ValueError, match="candidate_id"):
        replace(eligible, candidate_id="pair-signal-candidate-forged")
    with pytest.raises(ValueError, match="eligibility"):
        replace(
            eligible,
            eligibility=PairSignalCandidateEligibility.INELIGIBLE,
            rejection_reason=PairSignalCandidateRejectionReason.STALE_AT_AS_OF,
        )
