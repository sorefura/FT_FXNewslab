from dataclasses import fields
from datetime import timedelta

import pytest
from fx_core import CurrencyPair
from fx_signal_store import (
    PAIR_SIGNAL_MATERIALIZATION_CLAIM_VERSION,
    PAIR_SIGNAL_MATERIALIZATION_COMPLETION_VERSION,
    PAIR_SIGNAL_MATERIALIZER_RESULT_VERSION,
    SIGNAL_STORE_ENTRY_VERSION,
    PairSignalDerivation,
    PairSignalMaterializationClaim,
    PairSignalMaterializationCompletion,
    PairSignalMaterializationCompletionDisposition,
    PairSignalMaterializationPersistenceResult,
    PairSignalMaterializationRequest,
    PairSignalMaterializationSpecification,
    PairSignalMaterializerOutcome,
    PairSignalMaterializerResult,
    PairSignalSelectionOutcome,
    PairSignalSelectionPersistenceDisposition,
    PairSignalSelectionPersistenceResult,
    PairSignalSelectionSnapshot,
    SignalContentSnapshot,
    SignalStorageOrigin,
    SignalStoreEntry,
    SourceSignalRole,
    reconstruct_materialized_pair_signal,
    resolve_pair_signal_selection,
)
from swap_bot.adoption import RuntimeMode
from swap_bot.execution_authority import ExecutionAuthorityMode
from swap_bot.signals.materialized_pair import (
    MaterializedPairSignalAuthorizationService,
    authorize_materialized_pair_signal,
)

from tests.pair_signal_materialization.factories import (
    NOW,
    candidate,
    pair_signal_snapshot,
    request,
    selected_snapshot,
    source_snapshot,
)
from tests.strategy_contracts.factories import strategy_config


class _RecordingGate:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def authorize(self, signal: object, **kwargs: object) -> object:
        self.calls.append({"signal": signal, **kwargs})
        return (signal, kwargs)


class _CountingMaterializer:
    def __init__(self, result: PairSignalMaterializerResult) -> None:
        self.result = result
        self.calls = 0

    def materialize(self, *_args: object, **_kwargs: object) -> PairSignalMaterializerResult:
        self.calls += 1
        return self.result


class _ForgedResult(PairSignalMaterializerResult):
    __slots__ = ()

    def validate_intrinsic_integrity(self) -> None:
        pass


class _ForgedCompletion(PairSignalMaterializationCompletion):
    __slots__ = ()

    def validate_intrinsic_integrity(self) -> None:
        pass


class _ForgedSelection(PairSignalSelectionPersistenceResult):
    __slots__ = ()


class _ForgedRequest(PairSignalMaterializationRequest):
    __slots__ = ()

    def validate_intrinsic_integrity(self) -> None:
        pass


class _ForgedSpecification(PairSignalMaterializationSpecification):
    __slots__ = ()

    def validate_intrinsic_integrity(self) -> None:
        pass


class _ForgedSelectionSnapshot(PairSignalSelectionSnapshot):
    __slots__ = ()

    def validate_intrinsic_integrity(self) -> None:
        pass


class _ForgedSignalSnapshot(SignalContentSnapshot):
    __slots__ = ()

    def validate_intrinsic_integrity(self) -> None:
        pass


def _clone(instance: object, **changes: object) -> object:
    clone = object.__new__(type(instance))
    for field in fields(instance):  # type: ignore[arg-type]
        object.__setattr__(
            clone, field.name, changes.get(field.name, getattr(instance, field.name))
        )
    return clone


def _clone_as(instance: object, target_type: type[object], **changes: object) -> object:
    clone = object.__new__(target_type)
    for field in fields(instance):  # type: ignore[arg-type]
        object.__setattr__(
            clone, field.name, changes.get(field.name, getattr(instance, field.name))
        )
    return clone


def _selected_result(
    outcome: PairSignalMaterializerOutcome = PairSignalMaterializerOutcome.MATERIALIZED,
) -> PairSignalMaterializerResult:
    selection = selected_snapshot()
    snapshot = pair_signal_snapshot(selection)
    claim = PairSignalMaterializationClaim(
        PAIR_SIGNAL_MATERIALIZATION_CLAIM_VERSION,
        selection.request,
        selection.checkpoint_sequence,
        selection.captured_at,
    )
    entry = SignalStoreEntry(
        SIGNAL_STORE_ENTRY_VERSION,
        1,
        snapshot.signal_id,
        snapshot.created_at,
        SignalStorageOrigin.PAIR_MATERIALIZATION,
    )
    completion = PairSignalMaterializationCompletion(
        PAIR_SIGNAL_MATERIALIZATION_COMPLETION_VERSION,
        selection.request,
        selection,
        PairSignalSelectionOutcome.SELECTED,
        snapshot,
        entry,
        PairSignalDerivation.create(
            pair_signal_snapshot=snapshot,
            selection_snapshot=selection,
            materialized_at=snapshot.created_at,
        ),
    )
    return PairSignalMaterializerResult(
        PAIR_SIGNAL_MATERIALIZER_RESULT_VERSION,
        selection.request,
        outcome,
        claim,
        PairSignalSelectionPersistenceResult(
            PairSignalSelectionPersistenceDisposition.INSERTED, selection
        ),
        PairSignalMaterializationPersistenceResult(
            PairSignalMaterializationCompletionDisposition.REUSED_IDENTICAL
            if outcome is PairSignalMaterializerOutcome.REUSED_IDENTICAL
            else PairSignalMaterializationCompletionDisposition.INSERTED,
            completion,
        ),
    )


def _non_selected_result(outcome: PairSignalMaterializerOutcome) -> PairSignalMaterializerResult:
    item = request()
    captured_at = NOW + timedelta(minutes=1)
    if outcome is PairSignalMaterializerOutcome.AMBIGUOUS:
        selected = selected_snapshot(materialization_request=item)
        base = selected.candidates[0]
        alternate = candidate(
            SourceSignalRole.BASE,
            materialization_request=item,
            snapshot=source_snapshot(SourceSignalRole.BASE, identifier="another-base"),
            store_sequence=3,
        )
        selection = resolve_pair_signal_selection(
            item, 3, captured_at, (base, selected.candidates[1], alternate)
        )
        assert selection.outcome is PairSignalSelectionOutcome.AMBIGUOUS
    else:
        selection = resolve_pair_signal_selection(item, 0, captured_at, ())
        assert selection.outcome is PairSignalSelectionOutcome.NO_MATCH
    completion = PairSignalMaterializationCompletion(
        PAIR_SIGNAL_MATERIALIZATION_COMPLETION_VERSION,
        item,
        selection,
        selection.outcome,
        None,
        None,
        None,
    )
    return PairSignalMaterializerResult(
        PAIR_SIGNAL_MATERIALIZER_RESULT_VERSION,
        item,
        outcome,
        PairSignalMaterializationClaim(
            PAIR_SIGNAL_MATERIALIZATION_CLAIM_VERSION,
            item,
            selection.checkpoint_sequence,
            selection.captured_at,
        ),
        PairSignalSelectionPersistenceResult(
            PairSignalSelectionPersistenceDisposition.INSERTED, selection
        ),
        PairSignalMaterializationPersistenceResult(
            PairSignalMaterializationCompletionDisposition.INSERTED, completion
        ),
    )


@pytest.mark.parametrize(
    "outcome",
    (PairSignalMaterializerOutcome.MATERIALIZED, PairSignalMaterializerOutcome.REUSED_IDENTICAL),
)
def test_reconstruction_matches_every_authenticated_snapshot_semantic_field(
    outcome: PairSignalMaterializerOutcome,
) -> None:
    result = _selected_result(outcome)
    signal = reconstruct_materialized_pair_signal(result)
    snapshot = result.pair_signal_snapshot
    assert snapshot is not None
    assert (
        signal.signal_id,
        signal.target.pair.symbol,
        signal.signal_type,
        signal.direction.value,
        signal.strength.value,
        signal.confidence.value,
        signal.horizon,
        signal.observed_at,
        signal.created_at,
        signal.versions,
        signal.source_feature_ids,
    ) == (
        snapshot.signal_id,
        snapshot.target_value,
        snapshot.signal_type,
        snapshot.direction_value,
        snapshot.strength,
        snapshot.confidence,
        snapshot.horizon,
        snapshot.observed_at,
        snapshot.created_at,
        type(signal.versions)(
            snapshot.producer_version,
            snapshot.model_version,
            snapshot.prompt_version,
            snapshot.scorer_version,
            snapshot.transformation_version,
        ),
        snapshot.source_feature_ids,
    )


@pytest.mark.parametrize(
    "outcome", (PairSignalMaterializerOutcome.NO_SELECTION, PairSignalMaterializerOutcome.AMBIGUOUS)
)
def test_non_selection_never_calls_the_adoption_gate(
    outcome: PairSignalMaterializerOutcome,
) -> None:
    gate = _RecordingGate()
    assert (
        authorize_materialized_pair_signal(
            _non_selected_result(outcome),
            config=strategy_config(),
            pair=CurrencyPair.parse("USD_JPY"),
            authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
            authorized_at=NOW + timedelta(minutes=3),
            adoption_gate=gate,  # type: ignore[arg-type]
        )
        is None
    )
    assert gate.calls == []


@pytest.mark.parametrize(
    "authority", (ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED, ExecutionAuthorityMode.PAPER)
)
def test_shadow_and_paper_request_shadow_adoption(authority: ExecutionAuthorityMode) -> None:
    result = _selected_result()
    config = strategy_config()
    authorized_at = NOW + timedelta(minutes=3)
    gate = _RecordingGate()
    authorize_materialized_pair_signal(
        result,
        config=config,
        pair=CurrencyPair.parse("USD_JPY"),
        authority=authority,
        authorized_at=authorized_at,
        adoption_gate=gate,  # type: ignore[arg-type]
    )
    assert gate.calls == [
        {
            "signal": reconstruct_materialized_pair_signal(result),
            "strategy_id": config.strategy_id,
            "strategy_version": config.strategy_version,
            "strategy_config_identity": config.strategy_config_identity,
            "runtime_mode": RuntimeMode.SHADOW,
            "authorized_at": authorized_at,
        }
    ]


def test_authorization_at_exact_signal_creation_time_calls_gate() -> None:
    result = _selected_result()
    reconstructed = reconstruct_materialized_pair_signal(result)
    gate = _RecordingGate()

    authorize_materialized_pair_signal(
        result,
        config=strategy_config(),
        pair=CurrencyPair.parse("USD_JPY"),
        authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
        authorized_at=reconstructed.created_at,
        adoption_gate=gate,  # type: ignore[arg-type]
    )

    assert len(gate.calls) == 1
    assert gate.calls[0]["signal"] == reconstructed
    assert gate.calls[0]["authorized_at"] == reconstructed.created_at


def test_live_stops_before_materializer_or_adoption() -> None:
    materializer = _CountingMaterializer(_selected_result())
    gate = _RecordingGate()
    service = MaterializedPairSignalAuthorizationService(
        materializer=materializer,
        adoption_gate=gate,  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="LIVE"):
        service.run(
            request(),
            config=strategy_config(),
            pair=CurrencyPair.parse("USD_JPY"),
            authority=ExecutionAuthorityMode.LIVE,
            authorized_at=NOW + timedelta(minutes=3),
            claim_captured_at=NOW + timedelta(minutes=1),
        )
    assert materializer.calls == 0
    assert gate.calls == []


def test_mismatch_or_future_evidence_stops_before_gate() -> None:
    result = _selected_result()
    gate = _RecordingGate()
    arguments = dict(
        config=strategy_config(),
        pair=CurrencyPair.parse("MXN_JPY"),
        authority=ExecutionAuthorityMode.PAPER,
        authorized_at=NOW + timedelta(minutes=3),
        adoption_gate=gate,
    )
    with pytest.raises(ValueError, match="Pair"):
        authorize_materialized_pair_signal(result, **arguments)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="predate"):
        authorize_materialized_pair_signal(
            result,
            config=strategy_config(),
            pair=CurrencyPair.parse("USD_JPY"),
            authority=ExecutionAuthorityMode.PAPER,
            authorized_at=NOW + timedelta(minutes=1),
            adoption_gate=gate,
        )  # type: ignore[arg-type]
    assert gate.calls == []


@pytest.mark.parametrize(
    "forged_specification",
    (
        {"output_signal_type": "other-pair-signal"},
        {"output_transformation_version": "other-transformation"},
    ),
)
def test_forged_signal_type_or_transformation_stops_before_gate(
    forged_specification: dict[str, str],
) -> None:
    result = _selected_result()
    gate = _RecordingGate()
    forged = _clone(
        result,
        request=_clone(
            result.request,
            specification=_clone(result.request.specification, **forged_specification),
        ),
    )
    with pytest.raises(ValueError):
        authorize_materialized_pair_signal(
            forged,
            config=strategy_config(),
            pair=CurrencyPair.parse("USD_JPY"),
            authority=ExecutionAuthorityMode.PAPER,
            authorized_at=NOW + timedelta(minutes=3),
            adoption_gate=gate,  # type: ignore[arg-type]
        )
    assert gate.calls == []


def test_forged_strategy_config_stops_before_gate() -> None:
    config = _clone(strategy_config(), strategy_id="")
    gate = _RecordingGate()
    with pytest.raises(ValueError, match="strategy_id"):
        authorize_materialized_pair_signal(
            _selected_result(),
            config=config,  # type: ignore[arg-type]
            pair=CurrencyPair.parse("USD_JPY"),
            authority=ExecutionAuthorityMode.PAPER,
            authorized_at=NOW + timedelta(minutes=3),
            adoption_gate=gate,  # type: ignore[arg-type]
        )
    assert gate.calls == []


def test_forged_result_completion_selection_request_and_specification_are_rejected() -> None:
    result = _selected_result()
    for forged in (
        _clone_as(result, _ForgedResult),
        _clone(
            result,
            completion_result=_clone(
                result.completion_result,
                completion=_clone_as(result.completion, _ForgedCompletion),
            ),
        ),
        _clone(
            result,
            selection_result=_clone_as(result.selection_result, _ForgedSelection),
        ),
        _clone(result, request=_clone_as(result.request, _ForgedRequest)),
        _clone(
            result,
            request=_clone(
                result.request,
                specification=_clone_as(result.request.specification, _ForgedSpecification),
            ),
        ),
    ):
        with pytest.raises((TypeError, ValueError)):
            reconstruct_materialized_pair_signal(forged)


def test_forged_selection_and_signal_snapshot_subclasses_are_rejected() -> None:
    result = _selected_result()
    forged_selection = _clone_as(result.selection_snapshot, _ForgedSelectionSnapshot)
    forged_selection_result = _clone(
        result.selection_result,
        selection_snapshot=forged_selection,
    )
    forged_selection_completion = _clone(
        result.completion,
        selection_snapshot=forged_selection,
    )
    forged_selection_result_root = _clone(
        result,
        selection_result=forged_selection_result,
        completion_result=_clone(
            result.completion_result,
            completion=forged_selection_completion,
        ),
    )
    forged_signal_result = _clone(
        result,
        completion_result=_clone(
            result.completion_result,
            completion=_clone(
                result.completion,
                pair_signal_snapshot=_clone_as(
                    result.pair_signal_snapshot,
                    _ForgedSignalSnapshot,
                ),
            ),
        ),
    )

    for forged in (forged_selection_result_root, forged_signal_result):
        with pytest.raises(TypeError, match="exact supported contract type"):
            reconstruct_materialized_pair_signal(forged)
