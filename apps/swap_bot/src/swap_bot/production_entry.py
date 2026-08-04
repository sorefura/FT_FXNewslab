from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from fx_core import Currency, CurrencyPair
from fx_core.time import require_utc
from fx_signal_store import (
    PairSignalMaterializationRequest,
    PairSignalMaterializerOutcome,
    PairSignalMaterializerResult,
)

from .adoption import digest
from .adoption_gate import LiveAdoptionGate
from .execution_authority import ExecutionAuthorityMode, require_execplan_0006_authority
from .operational_swap import (
    OperationalSwapResolution,
    OperationalSwapResolutionOutcome,
)
from .production_strategy_store import (
    ProductionEntryPersistenceResult,
    SQLiteProductionEntryStore,
)
from .signals.materialized_pair import authorize_materialized_pair_signal
from .strategy import (
    NewsFilteredCarryStrategy,
    NewsFilteredCarryStrategyConfig,
    ProductionEntryEvaluationInput,
)

PRODUCTION_ENTRY_WORK_ITEM_VERSION = "production-entry-work-item-v1"


class ProductionEntryPreEvaluationReason(StrEnum):
    PAIR_NO_SELECTION = "PAIR_NO_SELECTION"
    PAIR_AMBIGUOUS = "PAIR_AMBIGUOUS"
    SWAP_MISSING = "SWAP_MISSING"
    SWAP_MALFORMED = "SWAP_MALFORMED"


class ProductionEntryPairOutcome(StrEnum):
    PRE_EVALUATION = "PRE_EVALUATION"
    EVALUATED = "EVALUATED"


@dataclass(frozen=True, slots=True)
class ProductionEntryWorkItem:
    work_item_id: str
    contract_version: str
    pair: CurrencyPair
    materialization_request: PairSignalMaterializationRequest
    claim_captured_at: datetime
    materialized_at_if_selected: datetime | None
    authority: ExecutionAuthorityMode
    authorized_at: datetime
    evaluated_at: datetime
    swap_resolution: OperationalSwapResolution

    def __post_init__(self) -> None:
        self.validate_intrinsic_integrity()

    @classmethod
    def create(
        cls,
        *,
        pair: CurrencyPair,
        materialization_request: PairSignalMaterializationRequest,
        claim_captured_at: datetime,
        materialized_at_if_selected: datetime | None,
        authority: ExecutionAuthorityMode,
        authorized_at: datetime,
        evaluated_at: datetime,
        swap_resolution: OperationalSwapResolution,
    ) -> ProductionEntryWorkItem:
        payload = _work_item_payload(
            pair=pair,
            materialization_request=materialization_request,
            claim_captured_at=claim_captured_at,
            materialized_at_if_selected=materialized_at_if_selected,
            authority=authority,
            authorized_at=authorized_at,
            evaluated_at=evaluated_at,
            swap_resolution=swap_resolution,
        )
        return cls(
            work_item_id="production-entry-work-item-" + digest(payload),
            contract_version=PRODUCTION_ENTRY_WORK_ITEM_VERSION,
            pair=pair,
            materialization_request=materialization_request,
            claim_captured_at=claim_captured_at,
            materialized_at_if_selected=materialized_at_if_selected,
            authority=authority,
            authorized_at=authorized_at,
            evaluated_at=evaluated_at,
            swap_resolution=swap_resolution,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _work_item_payload(
            pair=self.pair,
            materialization_request=self.materialization_request,
            claim_captured_at=self.claim_captured_at,
            materialized_at_if_selected=self.materialized_at_if_selected,
            authority=self.authority,
            authorized_at=self.authorized_at,
            evaluated_at=self.evaluated_at,
            swap_resolution=self.swap_resolution,
        )

    def validate_intrinsic_integrity(self) -> None:
        if type(self.work_item_id) is not str:
            raise TypeError("work_item_id must be exact str")
        if type(self.contract_version) is not str:
            raise TypeError("work item contract_version must be exact str")
        if self.contract_version != PRODUCTION_ENTRY_WORK_ITEM_VERSION:
            raise ValueError("unsupported ProductionEntryWorkItem contract")
        _validate_exact_pair(self.pair)
        if type(self.materialization_request) is not PairSignalMaterializationRequest:
            raise TypeError("work item requires exact PairSignalMaterializationRequest")
        PairSignalMaterializationRequest.validate_intrinsic_integrity(
            self.materialization_request
        )
        if (
            self.materialization_request.pair != self.pair
            or self.materialization_request.specification.pair != self.pair
        ):
            raise ValueError("work item Pair differs from materialization Request")
        for value, label in (
            (self.claim_captured_at, "claim_captured_at"),
            (self.authorized_at, "authorized_at"),
            (self.evaluated_at, "evaluated_at"),
        ):
            if type(value) is not datetime:
                raise TypeError(f"{label} must be exact datetime")
            require_utc(value, label)
        if self.claim_captured_at < self.materialization_request.as_of:
            raise ValueError("claim_captured_at cannot predate Request as_of")
        if self.materialized_at_if_selected is not None:
            if type(self.materialized_at_if_selected) is not datetime:
                raise TypeError("materialized_at_if_selected must be exact datetime or None")
            require_utc(self.materialized_at_if_selected, "materialized_at_if_selected")
            if self.materialized_at_if_selected < self.claim_captured_at:
                raise ValueError("selected materialization cannot predate Claim capture")
            if self.materialized_at_if_selected > self.authorized_at:
                raise ValueError("authorization cannot predate selected materialization")
        if self.authorized_at > self.evaluated_at:
            raise ValueError("evaluation cannot predate authorization")
        if type(self.authority) is not ExecutionAuthorityMode:
            raise TypeError("authority must be exact ExecutionAuthorityMode")
        if type(self.swap_resolution) is not OperationalSwapResolution:
            raise TypeError("work item requires exact OperationalSwapResolution")
        OperationalSwapResolution.validate_intrinsic_integrity(self.swap_resolution)
        if self.swap_resolution.pair != self.pair:
            raise ValueError("Swap resolution belongs to another work-item Pair")
        if self.work_item_id != "production-entry-work-item-" + digest(
            self.identity_payload
        ):
            raise ValueError("work_item_id does not match intrinsic content")


@dataclass(frozen=True, slots=True)
class ProductionEntryPairResult:
    result_id: str
    work_item_id: str
    pair: CurrencyPair
    outcome: ProductionEntryPairOutcome
    materializer_outcome: PairSignalMaterializerOutcome
    swap_resolution_id: str
    pre_evaluation_reason: ProductionEntryPreEvaluationReason | None
    persistence: ProductionEntryPersistenceResult | None

    def __post_init__(self) -> None:
        _validate_exact_pair(self.pair)
        for value, label in (
            (self.result_id, "result_id"),
            (self.work_item_id, "work_item_id"),
            (self.swap_resolution_id, "swap_resolution_id"),
        ):
            if type(value) is not str:
                raise TypeError(f"{label} must be exact str")
        if not self.work_item_id.strip() or not self.swap_resolution_id.strip():
            raise ValueError("Pair result lineage IDs must not be blank")
        if type(self.outcome) is not ProductionEntryPairOutcome:
            raise TypeError("Pair result outcome must be exact ProductionEntryPairOutcome")
        if type(self.materializer_outcome) is not PairSignalMaterializerOutcome:
            raise TypeError("materializer_outcome must be exact PairSignalMaterializerOutcome")
        if self.outcome is ProductionEntryPairOutcome.PRE_EVALUATION:
            if (
                type(self.pre_evaluation_reason) is not ProductionEntryPreEvaluationReason
                or self.persistence is not None
            ):
                raise ValueError("PRE_EVALUATION requires one reason and no persistence")
            result_identity = self.pre_evaluation_reason.value
        else:
            if (
                self.pre_evaluation_reason is not None
                or type(self.persistence) is not ProductionEntryPersistenceResult
            ):
                raise ValueError("EVALUATED requires exact persistence and no pre reason")
            ProductionEntryPersistenceResult.__post_init__(self.persistence)
            if self.persistence.evaluation.pair != self.pair:
                raise ValueError("persisted evaluation belongs to another Pair")
            result_identity = self.persistence.evaluation.evaluation_id
        payload = _pair_result_payload(
            work_item_id=self.work_item_id,
            pair=self.pair,
            materializer_outcome=self.materializer_outcome,
            resolution_id=self.swap_resolution_id,
            outcome=self.outcome,
            result_identity=result_identity,
        )
        if self.result_id != "production-entry-pair-result-" + digest(payload):
            raise ValueError("Pair result ID does not match intrinsic content")

    @classmethod
    def pre_evaluation(
        cls,
        work_item: ProductionEntryWorkItem,
        *,
        materializer_outcome: PairSignalMaterializerOutcome,
        reason: ProductionEntryPreEvaluationReason,
    ) -> ProductionEntryPairResult:
        payload = _pair_result_payload(
            work_item_id=work_item.work_item_id,
            pair=work_item.pair,
            materializer_outcome=materializer_outcome,
            resolution_id=work_item.swap_resolution.resolution_id,
            outcome=ProductionEntryPairOutcome.PRE_EVALUATION,
            result_identity=reason.value,
        )
        return cls(
            result_id="production-entry-pair-result-" + digest(payload),
            work_item_id=work_item.work_item_id,
            pair=work_item.pair,
            outcome=ProductionEntryPairOutcome.PRE_EVALUATION,
            materializer_outcome=materializer_outcome,
            swap_resolution_id=work_item.swap_resolution.resolution_id,
            pre_evaluation_reason=reason,
            persistence=None,
        )

    @classmethod
    def evaluated(
        cls,
        work_item: ProductionEntryWorkItem,
        *,
        materializer_outcome: PairSignalMaterializerOutcome,
        persistence: ProductionEntryPersistenceResult,
    ) -> ProductionEntryPairResult:
        payload = _pair_result_payload(
            work_item_id=work_item.work_item_id,
            pair=work_item.pair,
            materializer_outcome=materializer_outcome,
            resolution_id=work_item.swap_resolution.resolution_id,
            outcome=ProductionEntryPairOutcome.EVALUATED,
            result_identity=persistence.evaluation.evaluation_id,
        )
        return cls(
            result_id="production-entry-pair-result-" + digest(payload),
            work_item_id=work_item.work_item_id,
            pair=work_item.pair,
            outcome=ProductionEntryPairOutcome.EVALUATED,
            materializer_outcome=materializer_outcome,
            swap_resolution_id=work_item.swap_resolution.resolution_id,
            pre_evaluation_reason=None,
            persistence=persistence,
        )


@dataclass(frozen=True, slots=True)
class ProductionEntryApplicationResult:
    pair_results: tuple[ProductionEntryPairResult, ...]

    def __post_init__(self) -> None:
        if type(self.pair_results) is not tuple:
            raise TypeError("pair_results must be exact tuple")
        if len(self.pair_results) != 2:
            raise ValueError("M2-C application result requires exactly two Pair results")
        for result in self.pair_results:
            if type(result) is not ProductionEntryPairResult:
                raise TypeError("pair_results must contain exact ProductionEntryPairResult")
            ProductionEntryPairResult.__post_init__(result)


class _PairMaterializer(Protocol):
    def materialize(
        self,
        request: PairSignalMaterializationRequest,
        *,
        claim_captured_at: datetime,
        materialized_at_if_selected: datetime | None = None,
    ) -> PairSignalMaterializerResult: ...


class ProductionEntryApplicationService:
    def __init__(
        self,
        *,
        materializer: _PairMaterializer,
        adoption_gate: LiveAdoptionGate,
        persistence_store: SQLiteProductionEntryStore,
    ) -> None:
        self._materializer = materializer
        self._adoption_gate = adoption_gate
        self._persistence_store = persistence_store

    def run(
        self,
        *,
        config: NewsFilteredCarryStrategyConfig,
        work_items: tuple[ProductionEntryWorkItem, ...],
    ) -> ProductionEntryApplicationResult:
        _prevalidate_batch(config, work_items)
        for work_item in work_items:
            require_execplan_0006_authority(work_item.authority)

        results: list[ProductionEntryPairResult] = []
        for work_item in work_items:
            materialized = self._materializer.materialize(
                work_item.materialization_request,
                claim_captured_at=work_item.claim_captured_at,
                materialized_at_if_selected=work_item.materialized_at_if_selected,
            )
            _validate_materializer_result_for_work_item(work_item, materialized)
            authorized = authorize_materialized_pair_signal(
                materialized,
                config=config,
                pair=work_item.pair,
                authority=work_item.authority,
                authorized_at=work_item.authorized_at,
                adoption_gate=self._adoption_gate,
            )
            if authorized is None:
                reason = (
                    ProductionEntryPreEvaluationReason.PAIR_AMBIGUOUS
                    if materialized.outcome is PairSignalMaterializerOutcome.AMBIGUOUS
                    else ProductionEntryPreEvaluationReason.PAIR_NO_SELECTION
                )
                results.append(
                    ProductionEntryPairResult.pre_evaluation(
                        work_item,
                        materializer_outcome=materialized.outcome,
                        reason=reason,
                    )
                )
                continue

            resolution = work_item.swap_resolution
            OperationalSwapResolution.validate_intrinsic_integrity(resolution)
            if resolution.outcome is not OperationalSwapResolutionOutcome.EVIDENCE:
                reason = (
                    ProductionEntryPreEvaluationReason.SWAP_MALFORMED
                    if resolution.outcome is OperationalSwapResolutionOutcome.MALFORMED
                    else ProductionEntryPreEvaluationReason.SWAP_MISSING
                )
                results.append(
                    ProductionEntryPairResult.pre_evaluation(
                        work_item,
                        materializer_outcome=materialized.outcome,
                        reason=reason,
                    )
                )
                continue
            evidence = resolution.evidence
            if evidence is None:
                raise ValueError("EVIDENCE resolution lacks exact Swap Evidence")
            evaluation_input = ProductionEntryEvaluationInput(
                authorized_pair_signal=authorized,
                approved_strategy_config_identity=config.strategy_config_identity,
                evaluated_pair=work_item.pair,
                swap_evidence=evidence,
                evaluated_at=work_item.evaluated_at,
            )
            expected_evaluation = NewsFilteredCarryStrategy(config).evaluate_entry(
                evaluation_input
            )
            persistence = self._persistence_store.evaluate_and_persist(
                config=config,
                materializer_result=materialized,
                swap_resolution=resolution,
                evaluation_input=evaluation_input,
            )
            if type(persistence) is not ProductionEntryPersistenceResult:
                raise TypeError(
                    "persistence result must be exact ProductionEntryPersistenceResult"
                )
            ProductionEntryPersistenceResult.__post_init__(persistence)
            if persistence.evaluation != expected_evaluation:
                raise RuntimeError("B4 persisted evaluation differs from ordered Strategy result")
            results.append(
                ProductionEntryPairResult.evaluated(
                    work_item,
                    materializer_outcome=materialized.outcome,
                    persistence=persistence,
                )
            )
        return ProductionEntryApplicationResult(tuple(results))


def _validate_materializer_result_for_work_item(
    work_item: ProductionEntryWorkItem,
    result: object,
) -> PairSignalMaterializerResult:
    if type(result) is not PairSignalMaterializerResult:
        raise TypeError("materializer result must use the exact supported contract type")
    PairSignalMaterializerResult.validate_intrinsic_integrity(result)
    if result.request != work_item.materialization_request:
        raise ValueError("materializer result belongs to another work-item Request")
    if result.claim.captured_at != work_item.claim_captured_at:
        raise ValueError("materializer Claim time differs from the work item")
    expected_materialized_at = work_item.materialized_at_if_selected
    snapshot = result.pair_signal_snapshot
    if snapshot is not None:
        if expected_materialized_at is None:
            raise ValueError("selected materializer result requires a work-item time")
        if snapshot.created_at != expected_materialized_at:
            raise ValueError("materializer selected time differs from the work item")
    elif expected_materialized_at is not None:
        raise ValueError("non-selected materializer result requires absent work-item time")
    return result


def _prevalidate_batch(
    config: object, work_items: object
) -> tuple[ProductionEntryWorkItem, ...]:
    if type(config) is not NewsFilteredCarryStrategyConfig:
        raise TypeError("config must be exact NewsFilteredCarryStrategyConfig")
    NewsFilteredCarryStrategyConfig.__post_init__(config)
    if type(work_items) is not tuple:
        raise TypeError("work_items must be exact tuple")
    for work_item in work_items:
        if type(work_item) is not ProductionEntryWorkItem:
            raise TypeError("work_items must contain exact ProductionEntryWorkItem")
        ProductionEntryWorkItem.validate_intrinsic_integrity(work_item)
    pairs = tuple(work_item.pair for work_item in work_items)
    if pairs != config.eligible_pairs:
        raise ValueError("work_items must contain exactly one item per configured Pair in order")
    return work_items


def _work_item_payload(
    *,
    pair: CurrencyPair,
    materialization_request: PairSignalMaterializationRequest,
    claim_captured_at: datetime,
    materialized_at_if_selected: datetime | None,
    authority: ExecutionAuthorityMode,
    authorized_at: datetime,
    evaluated_at: datetime,
    swap_resolution: OperationalSwapResolution,
) -> dict[str, object]:
    return {
        "contract_version": PRODUCTION_ENTRY_WORK_ITEM_VERSION,
        "pair": pair.symbol,
        "materialization_request_id": materialization_request.request_id,
        "claim_captured_at": claim_captured_at.isoformat(),
        "materialized_at_if_selected": (
            None
            if materialized_at_if_selected is None
            else materialized_at_if_selected.isoformat()
        ),
        "authority": authority.value,
        "authorized_at": authorized_at.isoformat(),
        "evaluated_at": evaluated_at.isoformat(),
        "swap_resolution_id": swap_resolution.resolution_id,
    }


def _pair_result_payload(
    *,
    work_item_id: str,
    pair: CurrencyPair,
    materializer_outcome: PairSignalMaterializerOutcome,
    resolution_id: str,
    outcome: ProductionEntryPairOutcome,
    result_identity: str,
) -> dict[str, object]:
    return {
        "work_item_id": work_item_id,
        "pair": pair.symbol,
        "materializer_outcome": materializer_outcome.value,
        "swap_resolution_id": resolution_id,
        "outcome": outcome.value,
        "result_identity": result_identity,
    }


def _validate_exact_pair(pair: object) -> None:
    if type(pair) is not CurrencyPair:
        raise TypeError("Pair must be exact CurrencyPair")
    if type(pair.base) is not Currency or type(pair.quote) is not Currency:
        raise TypeError("Pair currencies must be exact Currency")
    Currency.__post_init__(pair.base)
    Currency.__post_init__(pair.quote)
    CurrencyPair.__post_init__(pair)
