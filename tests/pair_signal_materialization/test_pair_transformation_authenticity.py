from dataclasses import fields
from datetime import timedelta

import pytest
from fx_core import (
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
    PairSignalDerivation,
    SignalContentSnapshot,
    SignalLineage,
    expected_pair_signal_snapshot,
    validate_pair_signal_transformation,
)

from tests.pair_signal_materialization.factories import (
    NOW,
    pair_signal_snapshot,
    request,
    selected_snapshot,
)


def _forged_pair_snapshot(
    snapshot: SignalContentSnapshot,
    *,
    pair: CurrencyPair | None = None,
    signal_id: SignalId | None = None,
    signal_type: str | None = None,
    direction_value: float | None = None,
    strength: float | None = None,
    confidence: float | None = None,
    horizon: Horizon | None = None,
    observed_at_delta: timedelta | None = None,
    created_at_delta: timedelta | None = None,
    producer_version: str | None = None,
    model_version: str | None = None,
    prompt_version: str | None = None,
    scorer_version: str | None = None,
    transformation_version: str | None = None,
    source_feature_ids: tuple[FeatureId, ...] | None = None,
    source_observation_ids: tuple[ObservationId, ...] | None = None,
) -> SignalContentSnapshot:
    features = source_feature_ids or snapshot.source_feature_ids
    observations = source_observation_ids or snapshot.source_observation_ids
    signal = Signal(
        signal_id=signal_id or snapshot.signal_id,
        target=PairTarget(pair or CurrencyPair.parse(snapshot.target_value)),
        signal_type=signal_type or snapshot.signal_type,
        direction=PairScore(
            snapshot.direction_value if direction_value is None else direction_value
        ),
        strength=Probability(snapshot.strength if strength is None else strength),
        confidence=Probability(
            snapshot.confidence if confidence is None else confidence
        ),
        horizon=horizon or snapshot.horizon,
        observed_at=snapshot.observed_at + (observed_at_delta or timedelta()),
        created_at=snapshot.created_at + (created_at_delta or timedelta()),
        source_feature_ids=features,
        versions=VersionMetadata(
            producer_version=(
                snapshot.producer_version
                if producer_version is None
                else producer_version
            ),
            model_version=(
                snapshot.model_version if model_version is None else model_version
            ),
            prompt_version=(
                snapshot.prompt_version if prompt_version is None else prompt_version
            ),
            scorer_version=(
                snapshot.scorer_version if scorer_version is None else scorer_version
            ),
            transformation_version=(
                snapshot.transformation_version
                if transformation_version is None
                else transformation_version
            ),
        ),
    )
    return SignalContentSnapshot.from_signal(
        signal,
        SignalLineage(
            signal_id=signal.signal_id,
            feature_ids=features,
            observation_ids=observations,
        ),
    )


def _intrinsically_valid_forged_derivation(
    derivation: PairSignalDerivation,
    **changes: object,
) -> PairSignalDerivation:
    forged = object.__new__(PairSignalDerivation)
    for field in fields(derivation):
        object.__setattr__(forged, field.name, getattr(derivation, field.name))
    for name, value in changes.items():
        object.__setattr__(forged, name, value)
    object.__setattr__(forged, "derivation_id", "temporary")
    object.__setattr__(forged, "derivation_id", forged.expected_derivation_id)
    forged.validate_intrinsic_integrity()
    return forged


def test_expected_pair_signal_is_exact_repeatable_shared_transformer_output() -> None:
    selection = selected_snapshot()
    materialized_at = NOW + timedelta(minutes=2)

    first = expected_pair_signal_snapshot(
        selection,
        materialized_at=materialized_at,
    )
    second = expected_pair_signal_snapshot(
        selection,
        materialized_at=materialized_at,
    )
    derivation_first = PairSignalDerivation.create(
        pair_signal_snapshot=first,
        selection_snapshot=selection,
        materialized_at=materialized_at,
    )
    derivation_second = PairSignalDerivation.create(
        pair_signal_snapshot=second,
        selection_snapshot=selection,
        materialized_at=materialized_at,
    )

    validate_pair_signal_transformation(
        first,
        selection,
        materialized_at=materialized_at,
    )
    derivation_first.validate_against(first, selection)
    assert first == second
    assert first.signal_content_hash == second.signal_content_hash
    assert first.direction_value == 0.8999999999999999
    assert first.strength == 0.8
    assert first.confidence == 0.9
    assert first.observed_at == NOW - timedelta(hours=1)
    assert first.producer_version == "producer-v1"
    assert first.model_version == "model-v1"
    assert first.prompt_version == "prompt-v1"
    assert first.scorer_version == "fundamental-scorer-v1"
    assert first.transformation_version == "currency-pair-v1"
    assert derivation_first == derivation_second


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"direction_value": 0.1}, "direction_value"),
        ({"strength": 0.1}, "strength"),
        ({"confidence": 0.1}, "confidence"),
        ({"observed_at_delta": timedelta(seconds=-1)}, "observed_at"),
        ({"producer_version": "producer-forged"}, "producer_version"),
        ({"model_version": "model-forged"}, "model_version"),
        ({"prompt_version": "prompt-forged"}, "prompt_version"),
        ({"scorer_version": "scorer-forged"}, "scorer_version"),
        (
            {"transformation_version": "currency-pair-forged"},
            "transformation_version",
        ),
        ({"pair": CurrencyPair.parse("EUR_USD")}, "target_value"),
        ({"signal_type": "pair_forged"}, "signal_type"),
        ({"horizon": Horizon.DAY_1}, "horizon"),
        ({"created_at_delta": timedelta(seconds=1)}, "created_at"),
        (
            {"source_feature_ids": (FeatureId("feature-forged"),)},
            "source_feature_ids",
        ),
        (
            {"source_observation_ids": (ObservationId("observation-forged"),)},
            "source_observation_ids",
        ),
        ({"signal_id": SignalId("pair-signal-forged")}, "signal_id"),
    ],
)
def test_intrinsically_valid_forged_pair_signal_is_rejected_by_full_transformer_relation(
    changes: dict[str, object],
    field: str,
) -> None:
    selection = selected_snapshot()
    materialized_at = NOW + timedelta(minutes=2)
    valid = pair_signal_snapshot(selection, materialized_at=materialized_at)
    forged = _forged_pair_snapshot(valid, **changes)

    forged.validate_intrinsic_integrity()
    assert forged.signal_content_hash == forged.expected_signal_content_hash
    with pytest.raises(ValueError, match=rf"output mismatch: {field}$"):
        validate_pair_signal_transformation(
            forged,
            selection,
            materialized_at=materialized_at,
        )
    with pytest.raises(ValueError, match=rf"output mismatch: {field}$"):
        PairSignalDerivation.create(
            pair_signal_snapshot=forged,
            selection_snapshot=selection,
            materialized_at=materialized_at,
        )


def test_derivation_content_identity_alone_does_not_authorize_relational_mismatch() -> None:
    selection = selected_snapshot()
    pair_snapshot = pair_signal_snapshot(selection)
    derivation = PairSignalDerivation.create(
        pair_signal_snapshot=pair_snapshot,
        selection_snapshot=selection,
        materialized_at=pair_snapshot.created_at,
    )
    forged = _intrinsically_valid_forged_derivation(
        derivation,
        pair_signal_content_hash="signal-content-forged",
    )

    with pytest.raises(ValueError, match="pair_signal_content_hash"):
        forged.validate_against(pair_snapshot, selection)


def test_derivation_rejects_foreign_selection_and_pair_signal_snapshots() -> None:
    selection = selected_snapshot()
    pair_snapshot = pair_signal_snapshot(selection)
    derivation = PairSignalDerivation.create(
        pair_signal_snapshot=pair_snapshot,
        selection_snapshot=selection,
        materialized_at=pair_snapshot.created_at,
    )
    foreign_selection = selected_snapshot(
        materialization_request=request(as_of=NOW + timedelta(seconds=1)),
    )
    foreign_pair_snapshot = pair_signal_snapshot(
        selection,
        materialized_at=pair_snapshot.created_at + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="transformation output mismatch"):
        derivation.validate_against(pair_snapshot, foreign_selection)
    with pytest.raises(ValueError, match="transformation output mismatch"):
        derivation.validate_against(foreign_pair_snapshot, selection)


def test_derivation_rejects_forged_source_candidate_content_relation() -> None:
    selection = selected_snapshot()
    pair_snapshot = pair_signal_snapshot(selection)
    derivation = PairSignalDerivation.create(
        pair_signal_snapshot=pair_snapshot,
        selection_snapshot=selection,
        materialized_at=pair_snapshot.created_at,
    )
    forged = _intrinsically_valid_forged_derivation(
        derivation,
        base_signal_content_hash="signal-content-foreign-source",
    )

    with pytest.raises(ValueError, match="base_signal_content_hash"):
        forged.validate_against(pair_snapshot, selection)
