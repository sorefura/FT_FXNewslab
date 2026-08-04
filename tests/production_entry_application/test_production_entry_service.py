import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from fx_core import Currency, CurrencyPair, FeatureId
from fx_signal_store import (
    PAIR_SIGNAL_MATERIALIZATION_CLAIM_VERSION,
    PAIR_SIGNAL_MATERIALIZATION_COMPLETION_VERSION,
    PAIR_SIGNAL_MATERIALIZER_RESULT_VERSION,
    SIGNAL_STORE_ENTRY_VERSION,
    OperationalPairSignalMaterializer,
    PairSignalDerivation,
    PairSignalMaterializationClaim,
    PairSignalMaterializationCompletion,
    PairSignalMaterializationCompletionDisposition,
    PairSignalMaterializationPersistenceResult,
    PairSignalMaterializationRequest,
    PairSignalMaterializerOutcome,
    PairSignalMaterializerResult,
    PairSignalSelectionOutcome,
    PairSignalSelectionPersistenceDisposition,
    PairSignalSelectionPersistenceResult,
    SignalStorageOrigin,
    SignalStoreEntry,
    SourceSignalRole,
    SQLiteSignalStore,
    reconstruct_materialized_pair_signal,
    resolve_pair_signal_selection,
)
from swap_bot.adoption import (
    AdoptionMode,
    AuthorizedSignal,
    RuntimeMode,
    SignalAuthorization,
    StrictCohortIdentity,
)
from swap_bot.adoption_application import ApproveSignalAdoptionOnceService
from swap_bot.adoption_gate import LiveAdoptionGate
from swap_bot.adoption_store import SQLiteAdoptionStore
from swap_bot.execution_authority import ExecutionAuthorityMode
from swap_bot.operational_swap import (
    OperationalSwapResolution,
    OperationalSwapResolutionOutcome,
)
from swap_bot.production_entry import (
    ProductionEntryApplicationService,
    ProductionEntryPairOutcome,
    ProductionEntryPreEvaluationReason,
    ProductionEntryWorkItem,
)
from swap_bot.production_strategy_store import (
    ProductionEntryPersistenceDisposition,
    ProductionEntryPersistenceResult,
    SQLiteProductionEntryStore,
)
from swap_bot.research_evidence import SQLiteResearchValidationEvidenceSource
from swap_bot.strategy import NewsFilteredCarryStrategy

from tests.adoption_factories import (
    adoption_policy,
    cohort_payload,
    seed_research_evidence,
)
from tests.factories import feature, observation
from tests.pair_signal_materialization.factories import (
    NOW,
    candidate,
    pair_signal_snapshot,
    request,
    selected_snapshot,
    source_signal,
    source_snapshot,
    specification,
)
from tests.strategy_contracts.factories import strategy_config, swap_evidence


class _Materializer:
    def __init__(self, results: tuple[PairSignalMaterializerResult, ...]) -> None:
        self.results = {result.request.request_id: result for result in results}
        self.calls: list[str] = []

    def materialize(
        self,
        item: PairSignalMaterializationRequest,
        **_kwargs: object,
    ) -> PairSignalMaterializerResult:
        self.calls.append(item.request_id)
        return self.results[item.request_id]


class _ComparisonBypassStr(str):
    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False


class _Gate:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.runtime_modes: list[RuntimeMode] = []

    def authorize(self, signal, **kwargs: object) -> AuthorizedSignal:  # type: ignore[no-untyped-def]
        pair = signal.target.pair
        self.calls.append(pair.symbol)
        self.runtime_modes.append(kwargs["runtime_mode"])  # type: ignore[arg-type]
        authorization = SignalAuthorization(
            authorization_id="pending-authorization-id",
            signal_id=signal.signal_id.value,
            adoption_decision_id=f"adoption-approval-{pair.symbol.lower()}",
            evidence_snapshot_id=f"research-evidence-{pair.symbol.lower()}",
            adoption_policy_version=f"adoption-policy-{pair.symbol.lower()}",
            strategy_id=str(kwargs["strategy_id"]),
            strategy_version=str(kwargs["strategy_version"]),
            adoption_mode=AdoptionMode.SHADOW_ONLY,
            runtime_mode=kwargs["runtime_mode"],  # type: ignore[arg-type]
            authorized_at=kwargs["authorized_at"],  # type: ignore[arg-type]
        )
        authorization = replace(
            authorization,
            authorization_id=authorization.expected_authorization_id,
        )
        return AuthorizedSignal(signal, authorization)


class _Persistence:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.seen: set[str] = set()

    def evaluate_and_persist(self, **kwargs: object) -> ProductionEntryPersistenceResult:
        config = kwargs["config"]
        evaluation_input = kwargs["evaluation_input"]
        evaluation = NewsFilteredCarryStrategy(config).evaluate_entry(evaluation_input)  # type: ignore[arg-type]
        disposition = (
            ProductionEntryPersistenceDisposition.REUSED_IDENTICAL
            if evaluation.evaluation_id in self.seen
            else ProductionEntryPersistenceDisposition.INSERTED
        )
        self.seen.add(evaluation.evaluation_id)
        self.calls.append(evaluation.pair.symbol)
        return ProductionEntryPersistenceResult(disposition, evaluation)


def _request(pair: CurrencyPair) -> PairSignalMaterializationRequest:
    return request(pair=pair, specification=specification(pair=pair))


def _selected(
    pair: CurrencyPair,
    *,
    materialization_request: PairSignalMaterializationRequest | None = None,
) -> PairSignalMaterializerResult:
    item = materialization_request or _request(pair)
    base = candidate(
        SourceSignalRole.BASE,
        materialization_request=item,
        snapshot=source_snapshot(
            SourceSignalRole.BASE,
            identifier=f"signal-{pair.base.code.lower()}-1",
            target_currency=pair.base,
        ),
        store_sequence=1,
    )
    quote = candidate(
        SourceSignalRole.QUOTE,
        materialization_request=item,
        snapshot=source_snapshot(
            SourceSignalRole.QUOTE,
            identifier=f"signal-{pair.quote.code.lower()}-1",
            target_currency=pair.quote,
        ),
        store_sequence=2,
    )
    selection = selected_snapshot(
        materialization_request=item,
        base=base,
        quote=quote,
    )
    snapshot = pair_signal_snapshot(selection)
    completion = PairSignalMaterializationCompletion(
        PAIR_SIGNAL_MATERIALIZATION_COMPLETION_VERSION,
        item,
        selection,
        PairSignalSelectionOutcome.SELECTED,
        snapshot,
        SignalStoreEntry(
            SIGNAL_STORE_ENTRY_VERSION,
            1,
            snapshot.signal_id,
            snapshot.created_at,
            SignalStorageOrigin.PAIR_MATERIALIZATION,
        ),
        PairSignalDerivation.create(
            pair_signal_snapshot=snapshot,
            selection_snapshot=selection,
            materialized_at=snapshot.created_at,
        ),
    )
    return PairSignalMaterializerResult(
        PAIR_SIGNAL_MATERIALIZER_RESULT_VERSION,
        item,
        PairSignalMaterializerOutcome.MATERIALIZED,
        PairSignalMaterializationClaim(
            PAIR_SIGNAL_MATERIALIZATION_CLAIM_VERSION,
            item,
            selection.checkpoint_sequence,
            selection.captured_at,
        ),
        PairSignalSelectionPersistenceResult(
            PairSignalSelectionPersistenceDisposition.INSERTED,
            selection,
        ),
        PairSignalMaterializationPersistenceResult(
            PairSignalMaterializationCompletionDisposition.INSERTED,
            completion,
        ),
    )


def _no_selection(pair: CurrencyPair) -> PairSignalMaterializerResult:
    item = _request(pair)
    captured_at = NOW + timedelta(minutes=1)
    selection = resolve_pair_signal_selection(item, 0, captured_at, ())
    completion = PairSignalMaterializationCompletion(
        PAIR_SIGNAL_MATERIALIZATION_COMPLETION_VERSION,
        item,
        selection,
        PairSignalSelectionOutcome.NO_MATCH,
        None,
        None,
        None,
    )
    return PairSignalMaterializerResult(
        PAIR_SIGNAL_MATERIALIZER_RESULT_VERSION,
        item,
        PairSignalMaterializerOutcome.NO_SELECTION,
        PairSignalMaterializationClaim(
            PAIR_SIGNAL_MATERIALIZATION_CLAIM_VERSION,
            item,
            selection.checkpoint_sequence,
            selection.captured_at,
        ),
        PairSignalSelectionPersistenceResult(
            PairSignalSelectionPersistenceDisposition.INSERTED,
            selection,
        ),
        PairSignalMaterializationPersistenceResult(
            PairSignalMaterializationCompletionDisposition.INSERTED,
            completion,
        ),
    )


def _ambiguous(pair: CurrencyPair) -> PairSignalMaterializerResult:
    item = _request(pair)
    base = candidate(
        SourceSignalRole.BASE,
        materialization_request=item,
        snapshot=source_snapshot(
            SourceSignalRole.BASE,
            identifier=f"signal-{pair.base.code.lower()}-1",
            target_currency=pair.base,
        ),
        store_sequence=1,
    )
    quote = candidate(
        SourceSignalRole.QUOTE,
        materialization_request=item,
        snapshot=source_snapshot(
            SourceSignalRole.QUOTE,
            identifier=f"signal-{pair.quote.code.lower()}-1",
            target_currency=pair.quote,
        ),
        store_sequence=2,
    )
    alternate = candidate(
        SourceSignalRole.BASE,
        materialization_request=item,
        snapshot=source_snapshot(
            SourceSignalRole.BASE,
            identifier=f"signal-{pair.base.code.lower()}-2",
            target_currency=pair.base,
        ),
        store_sequence=3,
    )
    captured_at = NOW + timedelta(minutes=1)
    selection = resolve_pair_signal_selection(
        item, 3, captured_at, (base, quote, alternate)
    )
    completion = PairSignalMaterializationCompletion(
        PAIR_SIGNAL_MATERIALIZATION_COMPLETION_VERSION,
        item,
        selection,
        PairSignalSelectionOutcome.AMBIGUOUS,
        None,
        None,
        None,
    )
    return PairSignalMaterializerResult(
        PAIR_SIGNAL_MATERIALIZER_RESULT_VERSION,
        item,
        PairSignalMaterializerOutcome.AMBIGUOUS,
        PairSignalMaterializationClaim(
            PAIR_SIGNAL_MATERIALIZATION_CLAIM_VERSION,
            item,
            selection.checkpoint_sequence,
            selection.captured_at,
        ),
        PairSignalSelectionPersistenceResult(
            PairSignalSelectionPersistenceDisposition.INSERTED,
            selection,
        ),
        PairSignalMaterializationPersistenceResult(
            PairSignalMaterializationCompletionDisposition.INSERTED,
            completion,
        ),
    )
def _work_item(
    result: PairSignalMaterializerResult,
    *,
    resolution_outcome: OperationalSwapResolutionOutcome = (
        OperationalSwapResolutionOutcome.EVIDENCE
    ),
    authority: ExecutionAuthorityMode = ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
) -> ProductionEntryWorkItem:
    pair = result.request.pair
    evaluated_at = NOW + timedelta(minutes=4)
    evidence = swap_evidence(pair=pair)
    resolution = OperationalSwapResolution.create(
        pair=pair,
        source=evidence.source,
        source_version=evidence.source_version,
        requested_at=evaluated_at,
        outcome=resolution_outcome,
        reason_code=f"{resolution_outcome.value}_FIXTURE",
        evidence=(
            evidence
            if resolution_outcome is OperationalSwapResolutionOutcome.EVIDENCE
            else None
        ),
    )
    return ProductionEntryWorkItem.create(
        pair=pair,
        materialization_request=result.request,
        claim_captured_at=NOW + timedelta(minutes=1),
        materialized_at_if_selected=(
            NOW + timedelta(minutes=2)
            if result.pair_signal_snapshot is not None
            else None
        ),
        authority=authority,
        authorized_at=NOW + timedelta(minutes=3),
        evaluated_at=evaluated_at,
        swap_resolution=resolution,
    )


def _service(results: tuple[PairSignalMaterializerResult, ...]):  # type: ignore[no-untyped-def]
    materializer = _Materializer(results)
    gate = _Gate()
    persistence = _Persistence()
    service = ProductionEntryApplicationService(
        materializer=materializer,
        adoption_gate=gate,  # type: ignore[arg-type]
        persistence_store=persistence,  # type: ignore[arg-type]
    )
    return service, materializer, gate, persistence


def test_two_candidate_pair_results_are_preserved_in_config_order_and_replay() -> None:
    config = strategy_config()
    selected = tuple(_selected(pair) for pair in config.eligible_pairs)
    items = tuple(_work_item(result) for result in selected)
    service, materializer, gate, persistence = _service(selected)

    first = service.run(config=config, work_items=items)
    replay = service.run(config=config, work_items=items)

    assert tuple(result.pair for result in first.pair_results) == config.eligible_pairs
    assert all(
        result.outcome is ProductionEntryPairOutcome.EVALUATED
        for result in first.pair_results
    )
    assert all(result.persistence is not None for result in first.pair_results)
    assert all(
        result.persistence.evaluation.candidate is not None
        for result in first.pair_results
        if result.persistence
    )
    assert tuple(
        result.persistence.disposition
        for result in replay.pair_results
        if result.persistence
    ) == (
        ProductionEntryPersistenceDisposition.REUSED_IDENTICAL,
        ProductionEntryPersistenceDisposition.REUSED_IDENTICAL,
    )
    expected_calls = [result.request.request_id for result in selected] * 2
    assert materializer.calls == expected_calls
    assert gate.calls == [pair.symbol for pair in config.eligible_pairs] * 2
    assert persistence.calls == [pair.symbol for pair in config.eligible_pairs] * 2


def test_all_work_items_are_prevalidated_before_first_component_call() -> None:
    config = strategy_config()
    selected = tuple(_selected(pair) for pair in config.eligible_pairs)
    service, materializer, gate, persistence = _service(selected)
    reversed_items = tuple(reversed(tuple(_work_item(result) for result in selected)))

    with pytest.raises(ValueError, match="exactly one item per configured Pair in order"):
        service.run(config=config, work_items=reversed_items)

    assert materializer.calls == []
    assert gate.calls == []
    assert persistence.calls == []


def test_live_in_any_pair_stops_the_whole_batch_before_materialization() -> None:
    config = strategy_config()
    selected = tuple(_selected(pair) for pair in config.eligible_pairs)
    items = (
        _work_item(selected[0]),
        _work_item(selected[1], authority=ExecutionAuthorityMode.LIVE),
    )
    service, materializer, gate, persistence = _service(selected)

    with pytest.raises(ValueError, match="LIVE"):
        service.run(config=config, work_items=items)

    assert materializer.calls == []
    assert gate.calls == []
    assert persistence.calls == []


def test_pair_nonselection_and_swap_missing_are_typed_pre_evaluation_results() -> None:
    config = strategy_config()
    first = _no_selection(config.eligible_pairs[0])
    second = _selected(config.eligible_pairs[1])
    items = (
        _work_item(first),
        _work_item(second, resolution_outcome=OperationalSwapResolutionOutcome.MISSING),
    )
    service, materializer, gate, persistence = _service((first, second))

    result = service.run(config=config, work_items=items)

    assert tuple(item.pre_evaluation_reason for item in result.pair_results) == (
        ProductionEntryPreEvaluationReason.PAIR_NO_SELECTION,
        ProductionEntryPreEvaluationReason.SWAP_MISSING,
    )
    assert all(
        item.outcome is ProductionEntryPairOutcome.PRE_EVALUATION
        for item in result.pair_results
    )
    assert len(materializer.calls) == 2
    assert gate.calls == [config.eligible_pairs[1].symbol]
    assert persistence.calls == []
    assert result.pair_results[0].result_id != result.pair_results[1].result_id


def test_malformed_resolution_pair_mismatch_fails_during_batch_prevalidation() -> None:
    config = strategy_config()
    selected = tuple(_selected(pair) for pair in config.eligible_pairs)
    items = list(_work_item(result) for result in selected)
    wrong_resolution = OperationalSwapResolution.create(
        pair=config.eligible_pairs[1],
        source="recorded-swap-source",
        source_version="recorded-swap-v1",
        requested_at=NOW + timedelta(minutes=4),
        outcome=OperationalSwapResolutionOutcome.MISSING,
        reason_code="MISSING_FIXTURE",
        evidence=None,
    )
    forged = object.__new__(ProductionEntryWorkItem)
    for name in ProductionEntryWorkItem.__dataclass_fields__:
        object.__setattr__(
            forged,
            name,
            wrong_resolution if name == "swap_resolution" else getattr(items[0], name),
        )
    items[0] = forged
    service, materializer, gate, persistence = _service(selected)

    with pytest.raises(ValueError, match="another work-item Pair"):
        service.run(config=config, work_items=tuple(items))

    assert materializer.calls == []
    assert gate.calls == []
    assert persistence.calls == []


def test_no_selection_and_ambiguous_are_distinct_and_stop_before_adoption() -> None:
    config = strategy_config()
    first = _no_selection(config.eligible_pairs[0])
    second = _ambiguous(config.eligible_pairs[1])
    items = (_work_item(first), _work_item(second))
    service, materializer, gate, persistence = _service((first, second))

    result = service.run(config=config, work_items=items)

    assert tuple(item.pre_evaluation_reason for item in result.pair_results) == (
        ProductionEntryPreEvaluationReason.PAIR_NO_SELECTION,
        ProductionEntryPreEvaluationReason.PAIR_AMBIGUOUS,
    )
    assert len(materializer.calls) == 2
    assert gate.calls == []
    assert persistence.calls == []


@pytest.mark.parametrize(
    ("resolution_outcome", "expected_reason"),
    (
        (
            OperationalSwapResolutionOutcome.MISSING,
            ProductionEntryPreEvaluationReason.SWAP_MISSING,
        ),
        (
            OperationalSwapResolutionOutcome.MALFORMED,
            ProductionEntryPreEvaluationReason.SWAP_MALFORMED,
        ),
    ),
)
def test_missing_and_malformed_stop_after_adoption_before_strategy_store(
    resolution_outcome: OperationalSwapResolutionOutcome,
    expected_reason: ProductionEntryPreEvaluationReason,
) -> None:
    config = strategy_config()
    selected = tuple(_selected(pair) for pair in config.eligible_pairs)
    items = (
        _work_item(selected[0], resolution_outcome=resolution_outcome),
        _work_item(selected[1]),
    )
    service, _, gate, persistence = _service(selected)

    result = service.run(config=config, work_items=items)

    assert result.pair_results[0].pre_evaluation_reason is expected_reason
    assert result.pair_results[1].outcome is ProductionEntryPairOutcome.EVALUATED
    assert gate.calls == [pair.symbol for pair in config.eligible_pairs]
    assert persistence.calls == [config.eligible_pairs[1].symbol]


@pytest.mark.parametrize(
    "authority",
    (ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED, ExecutionAuthorityMode.PAPER),
)
def test_shadow_and_paper_both_request_shadow_adoption(
    authority: ExecutionAuthorityMode,
) -> None:
    config = strategy_config()
    selected = tuple(_selected(pair) for pair in config.eligible_pairs)
    items = tuple(_work_item(result, authority=authority) for result in selected)
    service, _, gate, _ = _service(selected)

    service.run(config=config, work_items=items)

    assert gate.runtime_modes == [RuntimeMode.SHADOW, RuntimeMode.SHADOW]


def test_work_item_identity_commits_stage_times_and_resolution() -> None:
    result = _selected(strategy_config().eligible_pairs[0])
    first = _work_item(result)
    identical = _work_item(result)
    later = ProductionEntryWorkItem.create(
        pair=first.pair,
        materialization_request=first.materialization_request,
        claim_captured_at=first.claim_captured_at,
        materialized_at_if_selected=first.materialized_at_if_selected,
        authority=first.authority,
        authorized_at=first.authorized_at,
        evaluated_at=first.evaluated_at + timedelta(microseconds=1),
        swap_resolution=first.swap_resolution,
    )

    assert first.work_item_id == identical.work_item_id
    assert later.work_item_id != first.work_item_id


def test_work_item_and_resolution_reject_comparison_bypassing_str_subclasses() -> None:
    item = _work_item(_selected(strategy_config().eligible_pairs[0]))
    config = strategy_config()
    evidence = swap_evidence(pair=item.pair)

    with pytest.raises(TypeError, match="contract_version must be exact str"):
        replace(item, contract_version=_ComparisonBypassStr("forged-version"))
    with pytest.raises(TypeError, match="work_item_id must be exact str"):
        replace(item, work_item_id=_ComparisonBypassStr("forged-work-item"))
    with pytest.raises(TypeError, match="resolution_id must be exact str"):
        replace(
            item.swap_resolution,
            resolution_id=_ComparisonBypassStr("forged-resolution"),
        )
    with pytest.raises(TypeError, match="config_contract_version must be exact str"):
        replace(
            config,
            config_contract_version=_ComparisonBypassStr("forged-config-version"),
        )
    with pytest.raises(TypeError, match="swap_evidence_id must be exact str"):
        replace(
            evidence,
            swap_evidence_id=_ComparisonBypassStr("forged-swap-evidence"),
        )


def test_invalid_stage_time_in_second_item_stops_before_first_call() -> None:
    config = strategy_config()
    selected = tuple(_selected(pair) for pair in config.eligible_pairs)
    items = [_work_item(result) for result in selected]
    forged = object.__new__(ProductionEntryWorkItem)
    for name in ProductionEntryWorkItem.__dataclass_fields__:
        object.__setattr__(
            forged,
            name,
            selected[1].request.as_of - timedelta(microseconds=1)
            if name == "claim_captured_at"
            else getattr(items[1], name),
        )
    items[1] = forged
    service, materializer, gate, persistence = _service(selected)

    with pytest.raises(ValueError, match="cannot predate Request as_of"):
        service.run(config=config, work_items=tuple(items))

    assert materializer.calls == []
    assert gate.calls == []
    assert persistence.calls == []


def test_materializer_result_for_another_request_stops_before_adoption() -> None:
    config = strategy_config()
    selected = tuple(_selected(pair) for pair in config.eligible_pairs)
    items = tuple(_work_item(result) for result in selected)
    wrong_request = request(
        pair=selected[0].request.pair,
        specification=selected[0].request.specification,
        as_of=selected[0].request.as_of - timedelta(microseconds=1),
    )
    wrong_result = _selected(
        selected[0].request.pair,
        materialization_request=wrong_request,
    )
    service, materializer, gate, persistence = _service(selected)
    materializer.results[selected[0].request.request_id] = wrong_result

    with pytest.raises(ValueError, match="another work-item Request"):
        service.run(config=config, work_items=items)

    assert materializer.calls == [selected[0].request.request_id]
    assert gate.calls == []
    assert persistence.calls == []


@pytest.mark.parametrize(
    ("changed_field", "expected_message"),
    (
        ("claim_captured_at", "Claim time differs from the work item"),
        ("materialized_at_if_selected", "selected time differs from the work item"),
    ),
)
def test_materializer_result_for_other_stage_times_stops_before_adoption(
    changed_field: str,
    expected_message: str,
) -> None:
    config = strategy_config()
    selected = tuple(_selected(pair) for pair in config.eligible_pairs)
    items = list(_work_item(result) for result in selected)
    first = items[0]
    values = {
        "pair": first.pair,
        "materialization_request": first.materialization_request,
        "claim_captured_at": first.claim_captured_at,
        "materialized_at_if_selected": first.materialized_at_if_selected,
        "authority": first.authority,
        "authorized_at": first.authorized_at,
        "evaluated_at": first.evaluated_at,
        "swap_resolution": first.swap_resolution,
    }
    stage_time = values[changed_field]
    assert stage_time is not None
    values[changed_field] = stage_time + timedelta(microseconds=1)
    items[0] = ProductionEntryWorkItem.create(**values)  # type: ignore[arg-type]
    service, materializer, gate, persistence = _service(selected)

    with pytest.raises(ValueError, match=expected_message):
        service.run(config=config, work_items=tuple(items))

    assert materializer.calls == [selected[0].request.request_id]
    assert gate.calls == []
    assert persistence.calls == []


def test_selected_materializer_result_requires_work_item_materialization_time() -> None:
    config = strategy_config()
    selected = tuple(_selected(pair) for pair in config.eligible_pairs)
    items = list(_work_item(result) for result in selected)
    first = items[0]
    items[0] = ProductionEntryWorkItem.create(
        pair=first.pair,
        materialization_request=first.materialization_request,
        claim_captured_at=first.claim_captured_at,
        materialized_at_if_selected=None,
        authority=first.authority,
        authorized_at=first.authorized_at,
        evaluated_at=first.evaluated_at,
        swap_resolution=first.swap_resolution,
    )
    service, materializer, gate, persistence = _service(selected)

    with pytest.raises(ValueError, match="requires a work-item time"):
        service.run(config=config, work_items=tuple(items))

    assert materializer.calls == [selected[0].request.request_id]
    assert gate.calls == []
    assert persistence.calls == []


@pytest.mark.parametrize("result_factory", (_no_selection, _ambiguous))
def test_non_selected_materializer_result_requires_absent_work_item_time(
    result_factory,  # type: ignore[no-untyped-def]
) -> None:
    config = strategy_config()
    first_result = result_factory(config.eligible_pairs[0])
    second_result = _selected(config.eligible_pairs[1])
    items = [_work_item(first_result), _work_item(second_result)]
    first = items[0]
    items[0] = ProductionEntryWorkItem.create(
        pair=first.pair,
        materialization_request=first.materialization_request,
        claim_captured_at=first.claim_captured_at,
        materialized_at_if_selected=NOW + timedelta(minutes=2),
        authority=first.authority,
        authorized_at=first.authorized_at,
        evaluated_at=first.evaluated_at,
        swap_resolution=first.swap_resolution,
    )
    service, materializer, gate, persistence = _service(
        (first_result, second_result)
    )

    with pytest.raises(ValueError, match="requires absent work-item time"):
        service.run(config=config, work_items=tuple(items))

    assert materializer.calls == [first_result.request.request_id]
    assert gate.calls == []
    assert persistence.calls == []


def test_first_pair_failure_propagates_without_retry_or_second_pair_run() -> None:
    config = strategy_config()
    selected = tuple(_selected(pair) for pair in config.eligible_pairs)
    items = tuple(_work_item(result) for result in selected)
    materializer = _Materializer(selected)
    gate = _Gate()

    class _FailingPersistence:
        def __init__(self) -> None:
            self.calls = 0

        def evaluate_and_persist(self, **_kwargs: object) -> ProductionEntryPersistenceResult:
            self.calls += 1
            raise RuntimeError("sqlite failure")

    persistence = _FailingPersistence()
    service = ProductionEntryApplicationService(
        materializer=materializer,
        adoption_gate=gate,  # type: ignore[arg-type]
        persistence_store=persistence,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="sqlite failure"):
        service.run(config=config, work_items=items)

    assert materializer.calls == [selected[0].request.request_id]
    assert gate.calls == [config.eligible_pairs[0].symbol]
    assert persistence.calls == 1


def test_forged_persistence_result_is_rejected_without_retry_or_second_pair_run() -> None:
    config = strategy_config()
    selected = tuple(_selected(pair) for pair in config.eligible_pairs)
    items = tuple(_work_item(result) for result in selected)
    materializer = _Materializer(selected)
    gate = _Gate()

    class _ForgedPersistence:
        def __init__(self) -> None:
            self.calls = 0

        def evaluate_and_persist(
            self, **kwargs: object
        ) -> ProductionEntryPersistenceResult:
            self.calls += 1
            evaluation = NewsFilteredCarryStrategy(kwargs["config"]).evaluate_entry(  # type: ignore[arg-type]
                kwargs["evaluation_input"]  # type: ignore[arg-type]
            )
            forged = object.__new__(ProductionEntryPersistenceResult)
            object.__setattr__(forged, "disposition", object())
            object.__setattr__(forged, "evaluation", evaluation)
            return forged

    persistence = _ForgedPersistence()
    service = ProductionEntryApplicationService(
        materializer=materializer,
        adoption_gate=gate,  # type: ignore[arg-type]
        persistence_store=persistence,  # type: ignore[arg-type]
    )

    with pytest.raises(
        TypeError,
        match="disposition must be exact ProductionEntryPersistenceDisposition",
    ):
        service.run(config=config, work_items=items)

    assert materializer.calls == [selected[0].request.request_id]
    assert gate.calls == [config.eligible_pairs[0].symbol]
    assert persistence.calls == 1


def test_real_adoption_and_b4_stores_converge_for_both_pairs(tmp_path: Path) -> None:
    config = strategy_config()
    signal_path = tmp_path / "signals.sqlite3"
    signal_store = SQLiteSignalStore(signal_path)
    sources: dict[str, tuple[Currency, SourceSignalRole]] = {}
    for pair in config.eligible_pairs:
        for currency, role in (
            (pair.base, SourceSignalRole.BASE),
            (pair.quote, SourceSignalRole.QUOTE),
        ):
            sources.setdefault(currency.code, (currency, role))
    observation_id = "observation-shared"
    signal_store.append_observation_if_absent(observation(observation_id))
    for index, (currency, role) in enumerate(sources.values(), start=1):
        role_order = "a" if role is SourceSignalRole.BASE else "z"
        feature_id = f"feature-{role_order}-{currency.code.lower()}"
        signal_store.append_feature(
            replace(
                feature(feature_id, observation_id),
                currency=currency,
            )
        )
        signal_store.append_signal(
            source_signal(
                role,
                identifier=f"signal-{currency.code.lower()}-1",
                target_currency=currency,
                feature_ids=(FeatureId(feature_id),),
            ),
            stored_at=NOW + timedelta(microseconds=index),
        )
    materializer = OperationalPairSignalMaterializer(signal_store)
    selected = tuple(
        materializer.materialize(
            _request(pair),
            claim_captured_at=NOW + timedelta(minutes=1),
            materialized_at_if_selected=NOW + timedelta(minutes=2),
        )
        for pair in config.eligible_pairs
    )
    assert tuple(result.outcome for result in selected) == (
        PairSignalMaterializerOutcome.MATERIALIZED,
        PairSignalMaterializerOutcome.MATERIALIZED,
    )
    assert all(
        result.selection_result.disposition
        is PairSignalSelectionPersistenceDisposition.INSERTED
        for result in selected
    )
    assert all(
        result.completion_result.disposition
        is PairSignalMaterializationCompletionDisposition.INSERTED
        for result in selected
    )
    live = tmp_path / "live.sqlite3"
    adoption_store = SQLiteAdoptionStore(live)
    for index, materialized in enumerate(selected):
        signal = reconstruct_materialized_pair_signal(materialized)
        pair = materialized.request.pair
        cohort = cohort_payload(
            signal_type=signal.signal_type,
            target_type="pair",
            target_value=pair.symbol,
            producer_version=signal.versions.producer_version,
            model_version=signal.versions.model_version,
            prompt_version=signal.versions.prompt_version,
            scorer_version=signal.versions.scorer_version,
            transformation_version=signal.versions.transformation_version,
        )
        research = tmp_path / f"research-{index}.sqlite3"
        assessment_id = f"assessment-pair-{index}"
        seed_research_evidence(
            research,
            assessment_id=assessment_id,
            cohort=cohort,
        )
        policy = adoption_policy(
            adoption_policy_version=f"adoption-policy-pair-{index}",
            strategy_id=config.strategy_id,
            strategy_version=config.strategy_version,
            strategy_config_identity=config.strategy_config_identity,
            expected_cohort=StrictCohortIdentity.from_payload(cohort),
            effective_from=signal.created_at - timedelta(minutes=1),
            expires_at=signal.created_at + timedelta(days=1),
        )
        ApproveSignalAdoptionOnceService(
            SQLiteResearchValidationEvidenceSource(research),
            clock=lambda signal=signal: signal.created_at - timedelta(minutes=1),
        ).run(
            assessment_id=assessment_id,
            policy=policy,
            approved_by="phase-reviewer",
            reason="validated pair",
            apply=True,
            store=adoption_store,
        )
    service = ProductionEntryApplicationService(
        materializer=materializer,
        adoption_gate=LiveAdoptionGate(adoption_store),
        persistence_store=SQLiteProductionEntryStore(live),
    )
    items = tuple(_work_item(result) for result in selected)

    first = service.run(config=config, work_items=items)
    replay = service.run(config=config, work_items=items)

    assert tuple(result.materializer_outcome for result in first.pair_results) == (
        PairSignalMaterializerOutcome.REUSED_IDENTICAL,
        PairSignalMaterializerOutcome.REUSED_IDENTICAL,
    )
    assert tuple(result.materializer_outcome for result in replay.pair_results) == (
        PairSignalMaterializerOutcome.REUSED_IDENTICAL,
        PairSignalMaterializerOutcome.REUSED_IDENTICAL,
    )
    assert tuple(
        result.persistence.disposition
        for result in first.pair_results
        if result.persistence
    ) == (
        ProductionEntryPersistenceDisposition.INSERTED,
        ProductionEntryPersistenceDisposition.INSERTED,
    )
    assert tuple(
        result.persistence.disposition
        for result in replay.pair_results
        if result.persistence
    ) == (
        ProductionEntryPersistenceDisposition.REUSED_IDENTICAL,
        ProductionEntryPersistenceDisposition.REUSED_IDENTICAL,
    )
    with sqlite3.connect(live) as connection:
        assert tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "live_signal_authorizations",
                "live_signal_authorization_content_commitments",
                "live_operational_swap_evidence",
                "live_news_filtered_carry_configs",
                "live_production_entry_evaluations",
                "live_production_trade_candidates",
                "live_candidates",
            )
        ) == (2, 2, 2, 1, 2, 2, 0)
    with sqlite3.connect(signal_path) as connection:
        assert tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "pair_signal_materialization_claims",
                "pair_signal_selection_snapshots",
                "pair_signal_selection_candidates",
                "pair_signal_derivations",
                "pair_signal_derivation_observations",
                "pair_signal_materialization_completions",
            )
        ) == (2, 2, 14, 2, 2, 2)
        assert connection.execute(
            "SELECT COUNT(*) FROM signals WHERE id LIKE 'pair-signal-%'"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM signal_store_entries "
            "WHERE storage_origin = 'PAIR_MATERIALIZATION'"
        ).fetchone()[0] == 2
