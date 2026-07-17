from datetime import UTC, datetime, timedelta

from fx_core import (
    Currency,
    CurrencyPair,
    CurrencyPairSignalTransformer,
    CurrencyTarget,
    DirectionScore,
    FeatureId,
    Horizon,
    ObservationId,
    Probability,
    Signal,
    SignalId,
    VersionMetadata,
)
from fx_signal_store import (
    PAIR_SIGNAL_MATERIALIZATION_REQUEST_VERSION,
    PAIR_SIGNAL_MATERIALIZATION_SPEC_VERSION,
    PAIR_SIGNAL_SELECTION_SNAPSHOT_VERSION,
    SUPPORTED_OBSERVATION_GROUP_POLICY_VERSION,
    SUPPORTED_OUTPUT_SIGNAL_TYPE,
    SUPPORTED_PAIR_TRANSFORMATION_VERSION,
    SUPPORTED_SELECTION_POLICY_VERSION,
    SUPPORTED_SOURCE_SIGNAL_TYPE,
    PairSignalMaterializationRequest,
    PairSignalMaterializationSpecification,
    PairSignalSelectionCandidate,
    PairSignalSelectionOutcome,
    PairSignalSelectionReason,
    PairSignalSelectionSnapshot,
    SignalContentSnapshot,
    SignalLineage,
    SourceSignalRole,
    expected_pair_signal_id,
    inspect_source_candidate,
)

NOW = datetime(2026, 7, 17, 6, 0, tzinfo=UTC)
PAIR = CurrencyPair.parse("USD_JPY")


def specification(**changes: object) -> PairSignalMaterializationSpecification:
    values: dict[str, object] = {
        "contract_version": PAIR_SIGNAL_MATERIALIZATION_SPEC_VERSION,
        "pair": PAIR,
        "source_signal_type": SUPPORTED_SOURCE_SIGNAL_TYPE,
        "output_signal_type": SUPPORTED_OUTPUT_SIGNAL_TYPE,
        "horizon": Horizon.DAYS_3,
        "producer_version": "producer-v1",
        "model_version": "model-v1",
        "prompt_version": "prompt-v1",
        "scorer_version": "fundamental-scorer-v1",
        "expected_source_transformation_version": None,
        "output_transformation_version": SUPPORTED_PAIR_TRANSFORMATION_VERSION,
        "source_signal_max_age": timedelta(hours=4),
        "observation_group_policy_version": (
            SUPPORTED_OBSERVATION_GROUP_POLICY_VERSION
        ),
        "selection_policy_version": SUPPORTED_SELECTION_POLICY_VERSION,
    }
    values.update(changes)
    return PairSignalMaterializationSpecification.create(**values)  # type: ignore[arg-type]


def request(**changes: object) -> PairSignalMaterializationRequest:
    values: dict[str, object] = {
        "contract_version": PAIR_SIGNAL_MATERIALIZATION_REQUEST_VERSION,
        "pair": PAIR,
        "as_of": NOW,
        "specification": specification(),
    }
    values.update(changes)
    return PairSignalMaterializationRequest.create(**values)  # type: ignore[arg-type]


def source_signal(
    role: SourceSignalRole = SourceSignalRole.BASE,
    *,
    identifier: str | None = None,
    target_currency: Currency | None = None,
    signal_type: str = SUPPORTED_SOURCE_SIGNAL_TYPE,
    horizon: Horizon = Horizon.DAYS_3,
    observed_at: datetime = NOW - timedelta(hours=1),
    created_at: datetime = NOW - timedelta(minutes=30),
    producer_version: str | None = "producer-v1",
    model_version: str | None = "model-v1",
    prompt_version: str | None = "prompt-v1",
    scorer_version: str = "fundamental-scorer-v1",
    transformation_version: str | None = None,
    direction: float | None = None,
    strength: float = 0.8,
    confidence: float = 0.9,
    feature_ids: tuple[FeatureId, ...] | None = None,
) -> Signal:
    currency = target_currency or (PAIR.base if role is SourceSignalRole.BASE else PAIR.quote)
    signal_id = identifier or f"signal-{role.value.lower()}-1"
    features = feature_ids or (FeatureId(f"feature-{role.value.lower()}-1"),)
    return Signal(
        signal_id=SignalId(signal_id),
        target=CurrencyTarget(currency),
        signal_type=signal_type,
        direction=DirectionScore(
            direction
            if direction is not None
            else (0.7 if role is SourceSignalRole.BASE else -0.2)
        ),
        strength=Probability(strength),
        confidence=Probability(confidence),
        horizon=horizon,
        observed_at=observed_at,
        created_at=created_at,
        source_feature_ids=features,
        versions=VersionMetadata(
            producer_version=producer_version,
            model_version=model_version,
            prompt_version=prompt_version,
            scorer_version=scorer_version,
            transformation_version=transformation_version,
        ),
    )


def source_snapshot(
    role: SourceSignalRole = SourceSignalRole.BASE,
    *,
    signal: Signal | None = None,
    observation_ids: tuple[ObservationId, ...] = (ObservationId("observation-1"),),
    lineage_feature_ids: tuple[FeatureId, ...] | None = None,
    **signal_changes: object,
) -> SignalContentSnapshot:
    item = signal or source_signal(role, **signal_changes)  # type: ignore[arg-type]
    return SignalContentSnapshot.from_signal(
        item,
        SignalLineage(
            signal_id=item.signal_id,
            feature_ids=lineage_feature_ids or item.source_feature_ids,
            observation_ids=observation_ids,
        ),
    )


def candidate(
    role: SourceSignalRole,
    *,
    materialization_request: PairSignalMaterializationRequest | None = None,
    snapshot: SignalContentSnapshot | None = None,
    store_sequence: int | None = None,
) -> PairSignalSelectionCandidate:
    selected_request = materialization_request or request()
    return inspect_source_candidate(
        selected_request,
        role,
        snapshot or source_snapshot(role),
        store_sequence or (1 if role is SourceSignalRole.BASE else 2),
    )


def selected_snapshot(
    *,
    materialization_request: PairSignalMaterializationRequest | None = None,
    base: PairSignalSelectionCandidate | None = None,
    quote: PairSignalSelectionCandidate | None = None,
    captured_at: datetime = NOW + timedelta(minutes=1),
    checkpoint_sequence: int = 2,
) -> PairSignalSelectionSnapshot:
    selected_request = materialization_request or request()
    base_candidate = base or candidate(
        SourceSignalRole.BASE,
        materialization_request=selected_request,
        store_sequence=1,
    )
    quote_candidate = quote or candidate(
        SourceSignalRole.QUOTE,
        materialization_request=selected_request,
        store_sequence=2,
    )
    return PairSignalSelectionSnapshot.create(
        contract_version=PAIR_SIGNAL_SELECTION_SNAPSHOT_VERSION,
        request=selected_request,
        checkpoint_sequence=checkpoint_sequence,
        captured_at=captured_at,
        candidates=(quote_candidate, base_candidate),
        outcome=PairSignalSelectionOutcome.SELECTED,
        reason=PairSignalSelectionReason.SELECTED_EXACT_GROUP,
        selected_base_candidate_id=base_candidate.candidate_id,
        selected_quote_candidate_id=quote_candidate.candidate_id,
        selected_base_signal_id=base_candidate.signal_snapshot.signal_id,
        selected_quote_signal_id=quote_candidate.signal_snapshot.signal_id,
        selected_observation_group_identity=base_candidate.observation_group_identity,
    )


def pair_signal_snapshot(
    selection: PairSignalSelectionSnapshot,
    *,
    materialized_at: datetime = NOW + timedelta(minutes=2),
) -> SignalContentSnapshot:
    base = next(
        item
        for item in selection.candidates
        if item.candidate_id == selection.selected_base_candidate_id
    )
    quote = next(
        item
        for item in selection.candidates
        if item.candidate_id == selection.selected_quote_candidate_id
    )
    base_signal = _signal_from_snapshot(base.signal_snapshot)
    quote_signal = _signal_from_snapshot(quote.signal_snapshot)
    pair_signal = CurrencyPairSignalTransformer().transform(
        base_signal,
        quote_signal,
        pair=selection.pair,
        signal_id=expected_pair_signal_id(
            selection.request,
            selection,
            materialized_at=materialized_at,
        ),
        created_at=materialized_at,
    )
    return SignalContentSnapshot.from_signal(
        pair_signal,
        SignalLineage(
            signal_id=pair_signal.signal_id,
            feature_ids=pair_signal.source_feature_ids,
            observation_ids=base.observation_ids,
        ),
    )


def _signal_from_snapshot(snapshot: SignalContentSnapshot) -> Signal:
    if snapshot.target_type.value != "currency":
        raise ValueError("source fixture requires a Currency Signal")
    return Signal(
        signal_id=snapshot.signal_id,
        target=CurrencyTarget(Currency(snapshot.target_value)),
        signal_type=snapshot.signal_type,
        direction=DirectionScore(snapshot.direction_value),
        strength=Probability(snapshot.strength),
        confidence=Probability(snapshot.confidence),
        horizon=snapshot.horizon,
        observed_at=snapshot.observed_at,
        created_at=snapshot.created_at,
        source_feature_ids=snapshot.source_feature_ids,
        versions=VersionMetadata(
            producer_version=snapshot.producer_version,
            model_version=snapshot.model_version,
            prompt_version=snapshot.prompt_version,
            scorer_version=snapshot.scorer_version,
            transformation_version=snapshot.transformation_version,
        ),
    )
