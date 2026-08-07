from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from fx_core import CurrencyPair, PairScore, PairTarget, Signal
from fx_core.time import require_utc

from ..adoption import AuthorizedSignal, SignalAuthorization, digest
from ..execution_authority import ExecutionAuthorityMode
from ..models import PositionId, Side
from ..swap import SwapAvailability
from .config import NewsFilteredCarryStrategyConfig
from .contracts import (
    PositionCloseCandidate,
    PositionExitEvaluationOutcome,
    PositionExitEvidenceContext,
    PositionExitKeepReason,
    PositionExitPositionEvidence,
    PositionExitReason,
    ProductionPositionExitEvaluation,
    ProductionPositionExitEvaluationInput,
)
from .versions import POSITION_CLOSE_CANDIDATE_CONTRACT_VERSION

if TYPE_CHECKING:
    from ..operational_swap import OperationalSwapResolution


class SignalAdoptionResolutionOutcome(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    NO_SELECTION = "NO_SELECTION"
    AMBIGUOUS = "AMBIGUOUS"
    ADOPTION_INACTIVE = "ADOPTION_INACTIVE"


@dataclass(frozen=True, slots=True)
class SignalAdoptionTerminalResolution:
    resolution_id: str
    outcome: SignalAdoptionResolutionOutcome
    signal_selection_checkpoint_id: str
    selection_request_id: str | None
    selection_claim_id: str | None
    selection_snapshot_id: str | None
    selection_completion_id: str | None
    prior_adoption_decision_id: str
    adoption_state_evidence_id: str
    reason_code: str
    resolved_at: datetime
    authorized_signal: AuthorizedSignal | None

    @classmethod
    def create(
        cls,
        *,
        outcome: SignalAdoptionResolutionOutcome,
        signal_selection_checkpoint_id: str,
        selection_request_id: str | None = None,
        selection_claim_id: str | None = None,
        selection_snapshot_id: str | None = None,
        selection_completion_id: str | None = None,
        prior_adoption_decision_id: str,
        adoption_state_evidence_id: str,
        reason_code: str,
        resolved_at: datetime,
        authorized_signal: AuthorizedSignal | None,
    ) -> SignalAdoptionTerminalResolution:
        payload = _signal_resolution_payload(
            outcome=outcome,
            signal_selection_checkpoint_id=signal_selection_checkpoint_id,
            selection_request_id=selection_request_id,
            selection_claim_id=selection_claim_id,
            selection_snapshot_id=selection_snapshot_id,
            selection_completion_id=selection_completion_id,
            prior_adoption_decision_id=prior_adoption_decision_id,
            adoption_state_evidence_id=adoption_state_evidence_id,
            reason_code=reason_code,
            resolved_at=resolved_at,
            authorized_signal=authorized_signal,
        )
        return cls(
            "signal-adoption-resolution-" + digest(payload),
            outcome,
            signal_selection_checkpoint_id,
            selection_request_id,
            selection_claim_id,
            selection_snapshot_id,
            selection_completion_id,
            prior_adoption_decision_id,
            adoption_state_evidence_id,
            reason_code,
            resolved_at,
            authorized_signal,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _signal_resolution_payload(
            outcome=self.outcome,
            signal_selection_checkpoint_id=self.signal_selection_checkpoint_id,
            selection_request_id=self.selection_request_id,
            selection_claim_id=self.selection_claim_id,
            selection_snapshot_id=self.selection_snapshot_id,
            selection_completion_id=self.selection_completion_id,
            prior_adoption_decision_id=self.prior_adoption_decision_id,
            adoption_state_evidence_id=self.adoption_state_evidence_id,
            reason_code=self.reason_code,
            resolved_at=self.resolved_at,
            authorized_signal=self.authorized_signal,
        )

    def validate_intrinsic_integrity(self) -> None:
        if type(self.resolution_id) is not str:
            raise TypeError("resolution_id must be exact str")
        if type(self.outcome) is not SignalAdoptionResolutionOutcome:
            raise TypeError("outcome must be exact SignalAdoptionResolutionOutcome")
        for value in (
            self.signal_selection_checkpoint_id,
            self.prior_adoption_decision_id,
            self.adoption_state_evidence_id,
            self.reason_code,
        ):
            _text(value)
        selection_ids = (
            self.selection_request_id,
            self.selection_claim_id,
            self.selection_snapshot_id,
            self.selection_completion_id,
        )
        if self.outcome is not SignalAdoptionResolutionOutcome.ADOPTION_INACTIVE:
            if any(value is None for value in selection_ids):
                raise ValueError("selection terminal outcomes require complete M2-B lineage")
        for selection_id in selection_ids:
            if selection_id is not None:
                _text(selection_id)
        _utc(self.resolved_at, "resolution resolved_at")
        if self.outcome is SignalAdoptionResolutionOutcome.AUTHORIZED:
            if type(self.authorized_signal) is not AuthorizedSignal:
                raise TypeError("AUTHORIZED requires exact AuthorizedSignal")
            _authorized(self.authorized_signal)
        elif self.authorized_signal is not None:
            raise ValueError("non-authorized resolution cannot contain a Signal")
        if self.resolution_id != "signal-adoption-resolution-" + digest(self.identity_payload):
            raise ValueError("resolution_id does not match content")

    def __post_init__(self) -> None:
        SignalAdoptionTerminalResolution.validate_intrinsic_integrity(self)


@dataclass(frozen=True, slots=True)
class PositionCloseCapacityEvidence:
    capacity_evidence_id: str
    capacity_contract_version: str
    position_id: PositionId
    position_evidence_id: str
    pair: CurrencyPair
    existing_position_side: Side
    position_observed_at: datetime
    open_quantity: Decimal
    quantity_unit: str
    source: str
    checkpoint_id: str

    @classmethod
    def create(
        cls,
        *,
        capacity_contract_version: str,
        position_id: PositionId,
        position_evidence_id: str,
        pair: CurrencyPair,
        existing_position_side: Side,
        position_observed_at: datetime,
        open_quantity: Decimal,
        quantity_unit: str,
        source: str,
        checkpoint_id: str,
    ) -> PositionCloseCapacityEvidence:
        payload = _capacity_payload(
            capacity_contract_version=capacity_contract_version,
            position_id=position_id,
            position_evidence_id=position_evidence_id,
            pair=pair,
            existing_position_side=existing_position_side,
            position_observed_at=position_observed_at,
            open_quantity=open_quantity,
            quantity_unit=quantity_unit,
            source=source,
            checkpoint_id=checkpoint_id,
        )
        return cls(
            "position-close-capacity-" + digest(payload),
            capacity_contract_version,
            position_id,
            position_evidence_id,
            pair,
            existing_position_side,
            position_observed_at,
            open_quantity,
            quantity_unit,
            source,
            checkpoint_id,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _capacity_payload(
            **{
                name: getattr(self, name)
                for name in (
                    "capacity_contract_version",
                    "position_id",
                    "position_evidence_id",
                    "pair",
                    "existing_position_side",
                    "position_observed_at",
                    "open_quantity",
                    "quantity_unit",
                    "source",
                    "checkpoint_id",
                )
            }
        )

    def __post_init__(self) -> None:
        if (
            type(self.capacity_evidence_id) is not str
            or type(self.capacity_contract_version) is not str
        ):
            raise TypeError("capacity identities must be exact str")
        if self.capacity_contract_version != "position-close-capacity-v1":
            raise ValueError("unsupported capacity contract")
        if (
            type(self.position_id) is not PositionId
            or type(self.pair) is not CurrencyPair
            or type(self.existing_position_side) is not Side
        ):
            raise TypeError("capacity Position, Pair, and Side must be exact types")
        for value in (
            self.position_evidence_id,
            self.quantity_unit,
            self.source,
            self.checkpoint_id,
        ):
            _text(value)
        if self.quantity_unit != "BASE_UNITS":
            raise ValueError("capacity quantity unit must be BASE_UNITS")
        _utc(self.position_observed_at, "capacity position_observed_at")
        if (
            type(self.open_quantity) is not Decimal
            or not self.open_quantity.is_finite()
            or self.open_quantity <= 0
        ):
            raise ValueError("capacity open_quantity must be positive finite Decimal")
        if self.capacity_evidence_id != "position-close-capacity-" + digest(self.identity_payload):
            raise ValueError("capacity_evidence_id does not match content")


@dataclass(frozen=True, slots=True)
class OrdinaryCloseAllocationPolicy:
    policy_version: str
    target_fraction: Decimal

    def __post_init__(self) -> None:
        _text(self.policy_version)
        if (
            type(self.target_fraction) is not Decimal
            or not self.target_fraction.is_finite()
            or not Decimal(0) < self.target_fraction <= Decimal(1)
        ):
            raise ValueError("target_fraction must be finite Decimal in (0, 1]")


@dataclass(frozen=True, slots=True)
class OrdinaryCloseRiskPolicy:
    policy_version: str
    maximum_capacity_age: timedelta

    def __post_init__(self) -> None:
        _text(self.policy_version)
        if type(
            self.maximum_capacity_age
        ) is not timedelta or self.maximum_capacity_age <= timedelta(0):
            raise ValueError("maximum_capacity_age must be positive")


@dataclass(frozen=True, slots=True)
class OrdinaryPositionExitWorkItem:
    work_item_id: str
    evaluation_input: ProductionPositionExitEvaluationInput
    capacity: PositionCloseCapacityEvidence
    signal_resolution: SignalAdoptionTerminalResolution
    swap_resolution: OperationalSwapResolution
    allocation_policy: OrdinaryCloseAllocationPolicy
    risk_policy: OrdinaryCloseRiskPolicy
    authority: ExecutionAuthorityMode

    @classmethod
    def create(
        cls,
        *,
        evaluation_input: ProductionPositionExitEvaluationInput,
        capacity: PositionCloseCapacityEvidence,
        signal_resolution: SignalAdoptionTerminalResolution,
        swap_resolution: OperationalSwapResolution,
        allocation_policy: OrdinaryCloseAllocationPolicy,
        risk_policy: OrdinaryCloseRiskPolicy,
        authority: ExecutionAuthorityMode,
    ) -> OrdinaryPositionExitWorkItem:
        payload = _work_payload(
            evaluation_input=evaluation_input,
            capacity=capacity,
            signal_resolution=signal_resolution,
            swap_resolution=swap_resolution,
            allocation_policy=allocation_policy,
            risk_policy=risk_policy,
            authority=authority,
        )
        return cls(
            "ordinary-position-exit-work-" + digest(payload),
            evaluation_input,
            capacity,
            signal_resolution,
            swap_resolution,
            allocation_policy,
            risk_policy,
            authority,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _work_payload(
            **{
                name: getattr(self, name)
                for name in (
                    "evaluation_input",
                    "capacity",
                    "signal_resolution",
                    "swap_resolution",
                    "allocation_policy",
                    "risk_policy",
                    "authority",
                )
            }
        )

    def validate_intrinsic_integrity(self) -> None:
        if (
            type(self.work_item_id) is not str
            or type(self.evaluation_input) is not ProductionPositionExitEvaluationInput
        ):
            raise TypeError("work item requires exact ID and evaluation input")
        ProductionPositionExitEvaluationInput.__post_init__(self.evaluation_input)
        _validate_exact_exit_input(self.evaluation_input)
        if (
            type(self.capacity) is not PositionCloseCapacityEvidence
            or type(self.signal_resolution) is not SignalAdoptionTerminalResolution
            or type(self.swap_resolution) is not _operational_swap_resolution_type()
        ):
            raise TypeError("work item requires exact operational evidence")
        PositionCloseCapacityEvidence.__post_init__(self.capacity)
        SignalAdoptionTerminalResolution.validate_intrinsic_integrity(self.signal_resolution)
        _operational_swap_resolution_type().validate_intrinsic_integrity(self.swap_resolution)
        if (
            type(self.allocation_policy) is not OrdinaryCloseAllocationPolicy
            or type(self.risk_policy) is not OrdinaryCloseRiskPolicy
            or type(self.authority) is not ExecutionAuthorityMode
        ):
            raise TypeError("work item policies and authority must be exact types")
        OrdinaryCloseAllocationPolicy.__post_init__(self.allocation_policy)
        OrdinaryCloseRiskPolicy.__post_init__(self.risk_policy)
        inp, cap = self.evaluation_input, self.capacity
        if (
            cap.position_id != inp.position_id
            or cap.position_evidence_id != inp.evidence_context.position_evidence_id
            or cap.pair != inp.pair
            or cap.existing_position_side is not inp.existing_position_side
            or cap.position_observed_at != inp.evidence_context.position_observed_at
        ):
            raise ValueError("capacity does not match Position evidence")
        if (
            self.signal_resolution.signal_selection_checkpoint_id
            != inp.evidence_context.signal_selection_checkpoint_id
            or self.signal_resolution.prior_adoption_decision_id
            != inp.evidence_context.prior_adoption_decision_id
            or self.signal_resolution.adoption_state_evidence_id
            != inp.evidence_context.adoption_state_evidence_id
        ):
            raise ValueError("Signal resolution does not match input context")
        if self.signal_resolution.authorized_signal != inp.authorized_pair_signal:
            raise ValueError("Signal resolution does not match accepted input")
        if self.swap_resolution.evidence != inp.swap_evidence:
            raise ValueError("Swap resolution does not match accepted input")
        swap_evidence = self.swap_resolution.evidence
        authorized_signal = self.signal_resolution.authorized_signal
        if (
            self.swap_resolution.pair != inp.pair
            or self.swap_resolution.requested_at > inp.evaluated_at
            or cap.position_observed_at > inp.evaluated_at
            or self.signal_resolution.resolved_at > inp.evaluated_at
            or (swap_evidence is not None and swap_evidence.received_at > inp.evaluated_at)
            or (
                authorized_signal is not None
                and authorized_signal.signal.created_at > inp.evaluated_at
            )
        ):
            raise ValueError("operational evidence is future or cross-lineage")
        if self.work_item_id != "ordinary-position-exit-work-" + digest(self.identity_payload):
            raise ValueError("work_item_id does not match content")

    def __post_init__(self) -> None:
        self.validate_intrinsic_integrity()


@dataclass(frozen=True, slots=True)
class OperationalPositionExitEvaluationResult:
    operational_evaluation_id: str
    work_item_id: str
    evaluation: ProductionPositionExitEvaluation

    @classmethod
    def create(
        cls, work_item: OrdinaryPositionExitWorkItem, evaluation: ProductionPositionExitEvaluation
    ) -> OperationalPositionExitEvaluationResult:
        return cls(
            "operational-position-exit-evaluation-"
            + digest(
                {"work_item_id": work_item.work_item_id, "evaluation": evaluation.identity_payload}
            ),
            work_item.work_item_id,
            evaluation,
        )

    def __post_init__(self) -> None:
        if (
            type(self.operational_evaluation_id) is not str
            or type(self.work_item_id) is not str
            or type(self.evaluation) is not ProductionPositionExitEvaluation
        ):
            raise TypeError("operational result requires exact values")
        ProductionPositionExitEvaluation.__post_init__(self.evaluation)
        expected = "operational-position-exit-evaluation-" + digest(
            {"work_item_id": self.work_item_id, "evaluation": self.evaluation.identity_payload}
        )
        if self.operational_evaluation_id != expected:
            raise ValueError("operational_evaluation_id does not match content")


class OrdinaryPositionExitEvaluator:
    def __init__(self, config: NewsFilteredCarryStrategyConfig) -> None:
        if type(config) is not NewsFilteredCarryStrategyConfig:
            raise TypeError("config must be exact NewsFilteredCarryStrategyConfig")
        NewsFilteredCarryStrategyConfig.__post_init__(config)
        self._config = config

    def evaluate(
        self, work_item: OrdinaryPositionExitWorkItem
    ) -> OperationalPositionExitEvaluationResult:
        if type(work_item) is not OrdinaryPositionExitWorkItem:
            raise TypeError("work_item must be exact OrdinaryPositionExitWorkItem")
        work_item.validate_intrinsic_integrity()
        if work_item.authority is ExecutionAuthorityMode.LIVE:
            raise ValueError("LIVE is not authorized")
        inp = work_item.evaluation_input
        if (
            inp.strategy_id != self._config.strategy_id
            or inp.strategy_version != self._config.strategy_version
            or inp.approved_strategy_config_identity != self._config.strategy_config_identity
            or inp.pair not in self._config.eligible_pairs
        ):
            raise ValueError("unsupported Strategy/config lineage")
        signal = work_item.signal_resolution.authorized_signal
        swap = work_item.swap_resolution.evidence
        normalized = ProductionPositionExitEvaluationInput(
            inp.strategy_id,
            inp.strategy_version,
            inp.approved_strategy_config_identity,
            inp.position_id,
            inp.pair,
            inp.existing_position_side,
            inp.evidence_context,
            signal,
            swap,
            inp.evaluated_at,
        )
        reason = self._reason(normalized, work_item.signal_resolution, work_item.swap_resolution)
        evaluation = (
            ProductionPositionExitEvaluation.create_keep(
                normalized, reason=PositionExitKeepReason.NO_EXIT_CONDITION
            )
            if reason is None
            else ProductionPositionExitEvaluation.create_close_candidate(
                normalized,
                close_candidate_contract_version=POSITION_CLOSE_CANDIDATE_CONTRACT_VERSION,
                exit_reason=reason,
            )
        )
        return OperationalPositionExitEvaluationResult.create(work_item, evaluation)

    def _reason(
        self,
        inp: ProductionPositionExitEvaluationInput,
        resolution: SignalAdoptionTerminalResolution,
        swap_resolution: OperationalSwapResolution,
    ) -> PositionExitReason | None:
        if resolution.outcome is SignalAdoptionResolutionOutcome.ADOPTION_INACTIVE:
            return PositionExitReason.ADOPTION_NO_LONGER_ACTIVE
        signal = resolution.authorized_signal
        if signal is not None:
            candidate = signal.signal
            if (
                type(candidate.target) is not PairTarget
                or type(candidate.direction) is not PairScore
                or candidate.target.pair != inp.pair
                or candidate.signal_type != self._config.expected_pair_signal_type
                or candidate.versions.transformation_version
                != self._config.pair_transformation_version
            ):
                raise ValueError("unsupported Signal lineage")
        stale_signal = (
            signal is not None
            and inp.evaluated_at - signal.signal.created_at > self._config.signal_max_age
        )
        if (signal is None or stale_signal) and self._config.close_on_missing_or_stale_signal:
            return PositionExitReason.REQUIRED_SIGNAL_MISSING_OR_STALE
        if signal is not None and not stale_signal:
            candidate = signal.signal
            reversed_ = (
                inp.existing_position_side is Side.BUY
                and candidate.direction.value < self._config.negative_entry_threshold.value
            ) or (
                inp.existing_position_side is Side.SELL
                and candidate.direction.value > self._config.positive_entry_threshold.value
            )
            if reversed_ and self._config.close_on_signal_reversal:
                return PositionExitReason.SIGNAL_REVERSED
        swap = swap_resolution.evidence
        unusable = (
            swap is None
            or swap.availability is not SwapAvailability.AVAILABLE
            or swap.received_at > inp.evaluated_at
            or inp.evaluated_at < swap.effective_from
            or (swap.effective_until is not None and inp.evaluated_at > swap.effective_until)
            or inp.evaluated_at - swap.received_at > self._config.swap_max_age
        )
        if unusable and self._config.close_on_missing_or_stale_swap:
            return PositionExitReason.REQUIRED_SWAP_MISSING_OR_STALE
        if not unusable and swap is not None:
            carry = (
                swap.long_received_amount
                if inp.existing_position_side is Side.BUY
                else swap.short_received_amount
            )
            if carry is None:
                raise ValueError("available Swap requires carry")
            if carry <= 0 and self._config.close_on_non_positive_carry:
                return PositionExitReason.CARRY_NO_LONGER_POSITIVE
        if (
            self._config.maximum_holding_age is not None
            and inp.evaluated_at - inp.evidence_context.position_opened_at
            >= self._config.maximum_holding_age
        ):
            return PositionExitReason.MAXIMUM_HOLDING_AGE
        return None


class OrdinaryClosePortfolioDisposition(StrEnum):
    ACCEPT = "ACCEPT"
    REDUCE = "REDUCE"
    REJECT = "REJECT"


class OrdinaryCloseRiskOutcome(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class OrdinaryCloseRiskReason(StrEnum):
    APPROVED = "APPROVED"
    PORTFOLIO_REJECTED = "PORTFOLIO_REJECTED"
    CAPACITY_IN_FUTURE = "CAPACITY_IN_FUTURE"
    CAPACITY_STALE = "CAPACITY_STALE"
    NON_POSITIVE_QUANTITY = "NON_POSITIVE_QUANTITY"
    OVERCLOSE_QUANTITY = "OVERCLOSE_QUANTITY"


@dataclass(frozen=True, slots=True)
class OrdinaryCloseReservationEntry:
    intent_id: str
    quantity: Decimal

    def __post_init__(self) -> None:
        _text(self.intent_id)
        _positive_finite_decimal(self.quantity, "reservation entry quantity")


@dataclass(frozen=True, slots=True)
class OrdinaryCloseReservationSnapshot:
    position_id: PositionId
    entries: tuple[OrdinaryCloseReservationEntry, ...]

    def __post_init__(self) -> None:
        if type(self.position_id) is not PositionId:
            raise TypeError("reservation snapshot position_id must be exact PositionId")
        if type(self.entries) is not tuple:
            raise TypeError("reservation snapshot entries must be an exact tuple")
        for entry in self.entries:
            if type(entry) is not OrdinaryCloseReservationEntry:
                raise TypeError(
                    "reservation entries must be exact OrdinaryCloseReservationEntry"
                )
            OrdinaryCloseReservationEntry.__post_init__(entry)

    @property
    def total_reserved(self) -> Decimal:
        total = Decimal("0")
        for entry in self.entries:
            total += entry.quantity
        return total

    @property
    def identity_payload(self) -> dict[str, object]:
        return {
            "position_id": self.position_id.value,
            "entries": [
                {"intent_id": entry.intent_id, "quantity": str(entry.quantity)}
                for entry in self.entries
            ],
        }


@dataclass(frozen=True, slots=True)
class OrdinaryClosePortfolioDecision:
    portfolio_decision_id: str
    close_candidate_id: str
    operational_evaluation_id: str
    capacity_evidence_id: str
    allocation_policy: OrdinaryCloseAllocationPolicy
    reservation_snapshot: OrdinaryCloseReservationSnapshot
    target_quantity: Decimal
    available_before: Decimal
    disposition: OrdinaryClosePortfolioDisposition
    allocated_quantity: Decimal | None

    @classmethod
    def create(
        cls,
        *,
        operational_evaluation_id: str,
        candidate: PositionCloseCandidate,
        capacity: PositionCloseCapacityEvidence,
        allocation_policy: OrdinaryCloseAllocationPolicy,
        reservation_snapshot: OrdinaryCloseReservationSnapshot,
    ) -> OrdinaryClosePortfolioDecision:
        _text(operational_evaluation_id)
        if type(candidate) is not PositionCloseCandidate:
            raise TypeError("candidate must be exact PositionCloseCandidate")
        PositionCloseCandidate.__post_init__(candidate)
        if type(capacity) is not PositionCloseCapacityEvidence:
            raise TypeError("capacity must be exact PositionCloseCapacityEvidence")
        PositionCloseCapacityEvidence.__post_init__(capacity)
        if type(allocation_policy) is not OrdinaryCloseAllocationPolicy:
            raise TypeError("allocation_policy must be exact OrdinaryCloseAllocationPolicy")
        OrdinaryCloseAllocationPolicy.__post_init__(allocation_policy)
        if type(reservation_snapshot) is not OrdinaryCloseReservationSnapshot:
            raise TypeError(
                "reservation_snapshot must be exact OrdinaryCloseReservationSnapshot"
            )
        OrdinaryCloseReservationSnapshot.__post_init__(reservation_snapshot)
        if (
            candidate.position_id != capacity.position_id
            or candidate.pair != capacity.pair
            or candidate.existing_position_side is not capacity.existing_position_side
        ):
            raise ValueError("Candidate does not match capacity Position/Pair/Side")
        if reservation_snapshot.position_id != capacity.position_id:
            raise ValueError("reservation snapshot belongs to another Position")

        total_reserved = reservation_snapshot.total_reserved
        if total_reserved > capacity.open_quantity:
            raise ValueError("prior reservations already exceed observed open quantity")
        available_before = capacity.open_quantity - total_reserved
        target_quantity = capacity.open_quantity * allocation_policy.target_fraction

        allocated_quantity: Decimal | None
        if available_before <= 0:
            disposition = OrdinaryClosePortfolioDisposition.REJECT
            allocated_quantity = None
        elif available_before < target_quantity:
            disposition = OrdinaryClosePortfolioDisposition.REDUCE
            allocated_quantity = available_before
        else:
            disposition = OrdinaryClosePortfolioDisposition.ACCEPT
            allocated_quantity = target_quantity

        payload = _portfolio_decision_payload(
            close_candidate_id=candidate.close_candidate_id,
            operational_evaluation_id=operational_evaluation_id,
            capacity_evidence_id=capacity.capacity_evidence_id,
            allocation_policy=allocation_policy,
            reservation_snapshot=reservation_snapshot,
            target_quantity=target_quantity,
            available_before=available_before,
            disposition=disposition,
            allocated_quantity=allocated_quantity,
        )
        return cls(
            "ordinary-close-portfolio-decision-" + digest(payload),
            candidate.close_candidate_id,
            operational_evaluation_id,
            capacity.capacity_evidence_id,
            allocation_policy,
            reservation_snapshot,
            target_quantity,
            available_before,
            disposition,
            allocated_quantity,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _portfolio_decision_payload(
            close_candidate_id=self.close_candidate_id,
            operational_evaluation_id=self.operational_evaluation_id,
            capacity_evidence_id=self.capacity_evidence_id,
            allocation_policy=self.allocation_policy,
            reservation_snapshot=self.reservation_snapshot,
            target_quantity=self.target_quantity,
            available_before=self.available_before,
            disposition=self.disposition,
            allocated_quantity=self.allocated_quantity,
        )

    def __post_init__(self) -> None:
        if type(self.portfolio_decision_id) is not str:
            raise TypeError("portfolio_decision_id must be exact str")
        for value in (
            self.close_candidate_id,
            self.operational_evaluation_id,
            self.capacity_evidence_id,
        ):
            _text(value)
        if type(self.allocation_policy) is not OrdinaryCloseAllocationPolicy:
            raise TypeError("allocation_policy must be exact OrdinaryCloseAllocationPolicy")
        OrdinaryCloseAllocationPolicy.__post_init__(self.allocation_policy)
        if type(self.reservation_snapshot) is not OrdinaryCloseReservationSnapshot:
            raise TypeError(
                "reservation_snapshot must be exact OrdinaryCloseReservationSnapshot"
            )
        OrdinaryCloseReservationSnapshot.__post_init__(self.reservation_snapshot)
        if type(self.disposition) is not OrdinaryClosePortfolioDisposition:
            raise TypeError("disposition must be exact OrdinaryClosePortfolioDisposition")
        if type(self.target_quantity) is not Decimal or not self.target_quantity.is_finite():
            raise TypeError("target_quantity must be finite Decimal")
        if type(self.available_before) is not Decimal or not self.available_before.is_finite():
            raise TypeError("available_before must be finite Decimal")
        if self.disposition is OrdinaryClosePortfolioDisposition.REJECT:
            if self.allocated_quantity is not None:
                raise ValueError("REJECT cannot carry an allocated_quantity")
            if self.available_before > 0:
                raise ValueError("REJECT requires available_before <= 0")
        else:
            if self.available_before <= 0:
                raise ValueError("REDUCE/ACCEPT requires available_before > 0")
            if self.allocated_quantity is None:
                raise ValueError("REDUCE/ACCEPT requires allocated_quantity")
            _positive_finite_decimal(self.allocated_quantity, "allocated_quantity")
            if self.disposition is OrdinaryClosePortfolioDisposition.REDUCE:
                if (
                    self.allocated_quantity != self.available_before
                    or self.available_before >= self.target_quantity
                ):
                    raise ValueError("REDUCE must allocate exactly available_before")
            else:
                if (
                    self.allocated_quantity != self.target_quantity
                    or self.available_before < self.target_quantity
                ):
                    raise ValueError("ACCEPT must allocate exactly target_quantity")
        expected_id = "ordinary-close-portfolio-decision-" + digest(self.identity_payload)
        if self.portfolio_decision_id != expected_id:
            raise ValueError("portfolio_decision_id does not match content")


@dataclass(frozen=True, slots=True)
class OrdinaryCloseRiskDecision:
    risk_decision_id: str
    portfolio_decision_id: str
    risk_policy: OrdinaryCloseRiskPolicy
    outcome: OrdinaryCloseRiskOutcome
    reason: OrdinaryCloseRiskReason

    @classmethod
    def create(
        cls,
        *,
        portfolio_decision: OrdinaryClosePortfolioDecision,
        candidate: PositionCloseCandidate,
        capacity: PositionCloseCapacityEvidence,
        reservation_snapshot: OrdinaryCloseReservationSnapshot,
        risk_policy: OrdinaryCloseRiskPolicy,
        evaluated_at: datetime,
    ) -> OrdinaryCloseRiskDecision:
        if type(portfolio_decision) is not OrdinaryClosePortfolioDecision:
            raise TypeError("portfolio_decision must be exact OrdinaryClosePortfolioDecision")
        OrdinaryClosePortfolioDecision.__post_init__(portfolio_decision)
        if type(candidate) is not PositionCloseCandidate:
            raise TypeError("candidate must be exact PositionCloseCandidate")
        PositionCloseCandidate.__post_init__(candidate)
        if type(capacity) is not PositionCloseCapacityEvidence:
            raise TypeError("capacity must be exact PositionCloseCapacityEvidence")
        PositionCloseCapacityEvidence.__post_init__(capacity)
        if type(reservation_snapshot) is not OrdinaryCloseReservationSnapshot:
            raise TypeError(
                "reservation_snapshot must be exact OrdinaryCloseReservationSnapshot"
            )
        OrdinaryCloseReservationSnapshot.__post_init__(reservation_snapshot)
        if type(risk_policy) is not OrdinaryCloseRiskPolicy:
            raise TypeError("risk_policy must be exact OrdinaryCloseRiskPolicy")
        OrdinaryCloseRiskPolicy.__post_init__(risk_policy)
        _utc(evaluated_at, "risk evaluated_at")

        if portfolio_decision.close_candidate_id != candidate.close_candidate_id:
            raise ValueError("Risk candidate does not match Portfolio decision")
        if portfolio_decision.capacity_evidence_id != capacity.capacity_evidence_id:
            raise ValueError("Risk capacity does not match Portfolio decision")
        if reservation_snapshot.position_id != capacity.position_id:
            raise ValueError("reservation snapshot belongs to another Position")
        if reservation_snapshot != portfolio_decision.reservation_snapshot:
            raise ValueError("Risk reservation snapshot does not match Portfolio decision")
        if (
            candidate.position_id != capacity.position_id
            or candidate.pair != capacity.pair
            or candidate.existing_position_side is not capacity.existing_position_side
        ):
            raise ValueError("Candidate does not match capacity Position/Pair/Side")

        if portfolio_decision.disposition is OrdinaryClosePortfolioDisposition.REJECT:
            outcome = OrdinaryCloseRiskOutcome.REJECT
            reason = OrdinaryCloseRiskReason.PORTFOLIO_REJECTED
        else:
            age = evaluated_at - capacity.position_observed_at
            quantity = portfolio_decision.allocated_quantity
            if age < timedelta(0):
                outcome = OrdinaryCloseRiskOutcome.REJECT
                reason = OrdinaryCloseRiskReason.CAPACITY_IN_FUTURE
            elif age > risk_policy.maximum_capacity_age:
                outcome = OrdinaryCloseRiskOutcome.REJECT
                reason = OrdinaryCloseRiskReason.CAPACITY_STALE
            elif quantity is None or quantity <= 0:
                outcome = OrdinaryCloseRiskOutcome.REJECT
                reason = OrdinaryCloseRiskReason.NON_POSITIVE_QUANTITY
            else:
                total_reserved = reservation_snapshot.total_reserved
                available_before = capacity.open_quantity - total_reserved
                if (
                    quantity > available_before
                    or total_reserved + quantity > capacity.open_quantity
                ):
                    outcome = OrdinaryCloseRiskOutcome.REJECT
                    reason = OrdinaryCloseRiskReason.OVERCLOSE_QUANTITY
                else:
                    outcome = OrdinaryCloseRiskOutcome.APPROVE
                    reason = OrdinaryCloseRiskReason.APPROVED

        payload = _risk_decision_payload(
            portfolio_decision_id=portfolio_decision.portfolio_decision_id,
            risk_policy=risk_policy,
            outcome=outcome,
            reason=reason,
        )
        return cls(
            "ordinary-close-risk-decision-" + digest(payload),
            portfolio_decision.portfolio_decision_id,
            risk_policy,
            outcome,
            reason,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _risk_decision_payload(
            portfolio_decision_id=self.portfolio_decision_id,
            risk_policy=self.risk_policy,
            outcome=self.outcome,
            reason=self.reason,
        )

    def __post_init__(self) -> None:
        if type(self.risk_decision_id) is not str:
            raise TypeError("risk_decision_id must be exact str")
        _text(self.portfolio_decision_id)
        if type(self.risk_policy) is not OrdinaryCloseRiskPolicy:
            raise TypeError("risk_policy must be exact OrdinaryCloseRiskPolicy")
        OrdinaryCloseRiskPolicy.__post_init__(self.risk_policy)
        if type(self.outcome) is not OrdinaryCloseRiskOutcome:
            raise TypeError("outcome must be exact OrdinaryCloseRiskOutcome")
        if type(self.reason) is not OrdinaryCloseRiskReason:
            raise TypeError("reason must be exact OrdinaryCloseRiskReason")
        if (self.outcome is OrdinaryCloseRiskOutcome.APPROVE) != (
            self.reason is OrdinaryCloseRiskReason.APPROVED
        ):
            raise ValueError("APPROVE requires APPROVED reason and vice versa")
        expected_id = "ordinary-close-risk-decision-" + digest(self.identity_payload)
        if self.risk_decision_id != expected_id:
            raise ValueError("risk_decision_id does not match content")


@dataclass(frozen=True, slots=True)
class ApprovedCloseIntent:
    close_candidate_id: str
    portfolio_decision_id: str
    risk_decision_id: str
    capacity_evidence_id: str
    position_id: PositionId
    pair: CurrencyPair
    side: Side
    quantity: Decimal
    authority: ExecutionAuthorityMode
    idempotency_key: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        candidate: PositionCloseCandidate,
        portfolio_decision: OrdinaryClosePortfolioDecision,
        risk_decision: OrdinaryCloseRiskDecision,
        capacity: PositionCloseCapacityEvidence,
        authority: ExecutionAuthorityMode,
        created_at: datetime,
    ) -> ApprovedCloseIntent:
        if type(candidate) is not PositionCloseCandidate:
            raise TypeError("candidate must be exact PositionCloseCandidate")
        PositionCloseCandidate.__post_init__(candidate)
        if type(portfolio_decision) is not OrdinaryClosePortfolioDecision:
            raise TypeError("portfolio_decision must be exact OrdinaryClosePortfolioDecision")
        OrdinaryClosePortfolioDecision.__post_init__(portfolio_decision)
        if type(risk_decision) is not OrdinaryCloseRiskDecision:
            raise TypeError("risk_decision must be exact OrdinaryCloseRiskDecision")
        OrdinaryCloseRiskDecision.__post_init__(risk_decision)
        if type(capacity) is not PositionCloseCapacityEvidence:
            raise TypeError("capacity must be exact PositionCloseCapacityEvidence")
        PositionCloseCapacityEvidence.__post_init__(capacity)
        if type(authority) is not ExecutionAuthorityMode:
            raise TypeError("authority must be exact ExecutionAuthorityMode")
        if authority not in (
            ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
            ExecutionAuthorityMode.PAPER,
        ):
            raise ValueError(
                "ApprovedCloseIntent authority must be SHADOW_NOT_SUBMITTED or PAPER"
            )
        _utc(created_at, "intent created_at")

        if risk_decision.outcome is not OrdinaryCloseRiskOutcome.APPROVE:
            raise ValueError("ApprovedCloseIntent requires a Risk APPROVE decision")
        if risk_decision.portfolio_decision_id != portfolio_decision.portfolio_decision_id:
            raise ValueError("Intent Risk decision does not match Portfolio decision")
        if portfolio_decision.close_candidate_id != candidate.close_candidate_id:
            raise ValueError("Intent candidate does not match Portfolio decision")
        if portfolio_decision.capacity_evidence_id != capacity.capacity_evidence_id:
            raise ValueError("Intent capacity does not match Portfolio decision")
        if (
            candidate.position_id != capacity.position_id
            or candidate.pair != capacity.pair
            or candidate.existing_position_side is not capacity.existing_position_side
        ):
            raise ValueError("Intent candidate does not match capacity Position/Pair/Side")

        quantity = portfolio_decision.allocated_quantity
        if quantity is None:
            raise ValueError("ApprovedCloseIntent requires an allocated quantity")
        side = candidate.close_side

        payload = _intent_payload(
            close_candidate_id=candidate.close_candidate_id,
            portfolio_decision_id=portfolio_decision.portfolio_decision_id,
            risk_decision_id=risk_decision.risk_decision_id,
            capacity_evidence_id=capacity.capacity_evidence_id,
            position_id=candidate.position_id,
            pair=candidate.pair,
            side=side,
            quantity=quantity,
        )
        return cls(
            candidate.close_candidate_id,
            portfolio_decision.portfolio_decision_id,
            risk_decision.risk_decision_id,
            capacity.capacity_evidence_id,
            candidate.position_id,
            candidate.pair,
            side,
            quantity,
            authority,
            "approved-close-intent-" + digest(payload),
            created_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _intent_payload(
            close_candidate_id=self.close_candidate_id,
            portfolio_decision_id=self.portfolio_decision_id,
            risk_decision_id=self.risk_decision_id,
            capacity_evidence_id=self.capacity_evidence_id,
            position_id=self.position_id,
            pair=self.pair,
            side=self.side,
            quantity=self.quantity,
        )

    def __post_init__(self) -> None:
        for value in (
            self.close_candidate_id,
            self.portfolio_decision_id,
            self.risk_decision_id,
            self.capacity_evidence_id,
            self.idempotency_key,
        ):
            _text(value)
        if type(self.position_id) is not PositionId:
            raise TypeError("position_id must be exact PositionId")
        if type(self.pair) is not CurrencyPair:
            raise TypeError("pair must be exact CurrencyPair")
        if type(self.side) is not Side:
            raise TypeError("side must be exact Side")
        _positive_finite_decimal(self.quantity, "quantity")
        if type(self.authority) is not ExecutionAuthorityMode:
            raise TypeError("authority must be exact ExecutionAuthorityMode")
        if self.authority not in (
            ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
            ExecutionAuthorityMode.PAPER,
        ):
            raise ValueError(
                "ApprovedCloseIntent authority must be SHADOW_NOT_SUBMITTED or PAPER"
            )
        _utc(self.created_at, "intent created_at")
        expected_key = "approved-close-intent-" + digest(self.identity_payload)
        if self.idempotency_key != expected_key:
            raise ValueError("idempotency_key does not match content")


def evaluate_ordinary_close_portfolio_and_risk(
    evaluation_result: OperationalPositionExitEvaluationResult,
    *,
    capacity: PositionCloseCapacityEvidence,
    reservation_snapshot: OrdinaryCloseReservationSnapshot,
    allocation_policy: OrdinaryCloseAllocationPolicy,
    risk_policy: OrdinaryCloseRiskPolicy,
    authority: ExecutionAuthorityMode,
) -> tuple[OrdinaryClosePortfolioDecision, OrdinaryCloseRiskDecision, ApprovedCloseIntent | None]:
    if type(evaluation_result) is not OperationalPositionExitEvaluationResult:
        raise TypeError(
            "evaluation_result must be exact OperationalPositionExitEvaluationResult"
        )
    evaluation = evaluation_result.evaluation
    if evaluation.outcome is not PositionExitEvaluationOutcome.CLOSE_CANDIDATE:
        raise ValueError("ordinary close Portfolio/Risk requires a CLOSE_CANDIDATE evaluation")
    candidate = evaluation.close_candidate
    if candidate is None:
        raise ValueError("CLOSE_CANDIDATE evaluation requires a close_candidate")

    portfolio_decision = OrdinaryClosePortfolioDecision.create(
        operational_evaluation_id=evaluation_result.operational_evaluation_id,
        candidate=candidate,
        capacity=capacity,
        allocation_policy=allocation_policy,
        reservation_snapshot=reservation_snapshot,
    )
    risk_decision = OrdinaryCloseRiskDecision.create(
        portfolio_decision=portfolio_decision,
        candidate=candidate,
        capacity=capacity,
        reservation_snapshot=reservation_snapshot,
        risk_policy=risk_policy,
        evaluated_at=evaluation.evaluated_at,
    )
    intent: ApprovedCloseIntent | None = None
    if risk_decision.outcome is OrdinaryCloseRiskOutcome.APPROVE:
        intent = ApprovedCloseIntent.create(
            candidate=candidate,
            portfolio_decision=portfolio_decision,
            risk_decision=risk_decision,
            capacity=capacity,
            authority=authority,
            created_at=evaluation.evaluated_at,
        )
    return portfolio_decision, risk_decision, intent


def _text(value: object) -> None:
    if type(value) is not str:
        raise TypeError("identity must be exact str")
    if not value.strip():
        raise ValueError("identity must not be blank")


def _utc(value: object, label: str) -> None:
    if type(value) is not datetime:
        raise TypeError(f"{label} must be exact datetime")
    require_utc(value, label)


def _authorized(value: AuthorizedSignal) -> None:
    if type(value.signal) is not Signal or type(value.authorization) is not SignalAuthorization:
        raise TypeError("AuthorizedSignal members must be exact types")
    value.authorization.validate_intrinsic_identity()


def _validate_exact_exit_input(value: ProductionPositionExitEvaluationInput) -> None:
    if (
        type(value.position_id) is not PositionId
        or type(value.pair) is not CurrencyPair
        or type(value.existing_position_side) is not Side
        or type(value.evidence_context) is not PositionExitEvidenceContext
    ):
        raise TypeError("exit input subject/context must use exact types")
    context = value.evidence_context
    if type(context.position) is not PositionExitPositionEvidence:
        raise TypeError("Position evidence must be exact PositionExitPositionEvidence")
    position = context.position
    if (
        type(position.position_id) is not PositionId
        or type(position.pair) is not CurrencyPair
        or type(position.existing_position_side) is not Side
    ):
        raise TypeError("Position evidence subject must use exact types")
    PositionExitPositionEvidence.__post_init__(position)
    for field in (
        context.signal_selection_checkpoint_id,
        context.swap_selection_checkpoint_id,
        context.expected_signal_specification_identity,
        context.prior_adoption_decision_id,
        context.adoption_state_evidence_id,
        context.exit_input_policy_version,
    ):
        _text(field)
    if value.authorized_pair_signal is not None:
        if type(value.authorized_pair_signal) is not AuthorizedSignal:
            raise TypeError("authorized signal must be exact AuthorizedSignal")
        _authorized(value.authorized_pair_signal)


def _positive_finite_decimal(value: object, label: str) -> None:
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        raise ValueError(f"{label} must be a positive finite Decimal")


def _portfolio_decision_payload(
    *,
    close_candidate_id: str,
    operational_evaluation_id: str,
    capacity_evidence_id: str,
    allocation_policy: OrdinaryCloseAllocationPolicy,
    reservation_snapshot: OrdinaryCloseReservationSnapshot,
    target_quantity: Decimal,
    available_before: Decimal,
    disposition: OrdinaryClosePortfolioDisposition,
    allocated_quantity: Decimal | None,
) -> dict[str, object]:
    return {
        "close_candidate_id": close_candidate_id,
        "operational_evaluation_id": operational_evaluation_id,
        "capacity_evidence_id": capacity_evidence_id,
        "allocation_policy": {
            "version": allocation_policy.policy_version,
            "target_fraction": str(allocation_policy.target_fraction),
        },
        "reservation_snapshot": reservation_snapshot.identity_payload,
        "target_quantity": str(target_quantity),
        "available_before": str(available_before),
        "disposition": disposition.value,
        "allocated_quantity": None if allocated_quantity is None else str(allocated_quantity),
    }


def _risk_decision_payload(
    *,
    portfolio_decision_id: str,
    risk_policy: OrdinaryCloseRiskPolicy,
    outcome: OrdinaryCloseRiskOutcome,
    reason: OrdinaryCloseRiskReason,
) -> dict[str, object]:
    return {
        "portfolio_decision_id": portfolio_decision_id,
        "risk_policy": {
            "version": risk_policy.policy_version,
            "maximum_capacity_age_us": int(
                risk_policy.maximum_capacity_age.total_seconds() * 1_000_000
            ),
        },
        "outcome": outcome.value,
        "reason": reason.value,
    }


def _intent_payload(
    *,
    close_candidate_id: str,
    portfolio_decision_id: str,
    risk_decision_id: str,
    capacity_evidence_id: str,
    position_id: PositionId,
    pair: CurrencyPair,
    side: Side,
    quantity: Decimal,
) -> dict[str, object]:
    return {
        "close_candidate_id": close_candidate_id,
        "portfolio_decision_id": portfolio_decision_id,
        "risk_decision_id": risk_decision_id,
        "capacity_evidence_id": capacity_evidence_id,
        "position_id": position_id.value,
        "pair": pair.symbol,
        "side": side.value,
        "quantity": str(quantity),
    }


def _operational_swap_resolution_type() -> type[OperationalSwapResolution]:
    from ..operational_swap import OperationalSwapResolution

    return OperationalSwapResolution


def _signal_resolution_payload(
    *,
    outcome: object,
    signal_selection_checkpoint_id: object,
    selection_request_id: str | None,
    selection_claim_id: str | None,
    selection_snapshot_id: str | None,
    selection_completion_id: str | None,
    prior_adoption_decision_id: object,
    adoption_state_evidence_id: object,
    reason_code: object,
    resolved_at: object,
    authorized_signal: AuthorizedSignal | None,
) -> dict[str, object]:
    return {
        "outcome": getattr(outcome, "value", outcome),
        "signal_selection_checkpoint_id": signal_selection_checkpoint_id,
        "selection_request_id": selection_request_id,
        "selection_claim_id": selection_claim_id,
        "selection_snapshot_id": selection_snapshot_id,
        "selection_completion_id": selection_completion_id,
        "prior_adoption_decision_id": prior_adoption_decision_id,
        "adoption_state_evidence_id": adoption_state_evidence_id,
        "reason_code": reason_code,
        "resolved_at": getattr(resolved_at, "isoformat", lambda: resolved_at)(),
        "authorized_signal": None
        if authorized_signal is None
        else {
            "signal": authorized_signal.signal.signal_id.value,
            "authorization": authorized_signal.authorization.authorization_id,
        },
    }


def _capacity_payload(
    *,
    capacity_contract_version: str,
    position_id: PositionId,
    position_evidence_id: str,
    pair: CurrencyPair,
    existing_position_side: Side,
    position_observed_at: datetime,
    open_quantity: Decimal,
    quantity_unit: str,
    source: str,
    checkpoint_id: str,
) -> dict[str, object]:
    return {
        "capacity_contract_version": capacity_contract_version,
        "position_id": position_id.value,
        "position_evidence_id": position_evidence_id,
        "pair": pair.symbol,
        "existing_position_side": existing_position_side.value,
        "position_observed_at": position_observed_at.isoformat(),
        "open_quantity": str(open_quantity),
        "quantity_unit": quantity_unit,
        "source": source,
        "checkpoint_id": checkpoint_id,
    }


def _work_payload(
    *,
    evaluation_input: ProductionPositionExitEvaluationInput,
    capacity: PositionCloseCapacityEvidence,
    signal_resolution: SignalAdoptionTerminalResolution,
    swap_resolution: OperationalSwapResolution,
    allocation_policy: OrdinaryCloseAllocationPolicy,
    risk_policy: OrdinaryCloseRiskPolicy,
    authority: ExecutionAuthorityMode,
) -> dict[str, object]:
    inp = evaluation_input
    cap = capacity
    res = signal_resolution
    swap = swap_resolution
    allocation = allocation_policy
    risk = risk_policy
    return {
        "input": {
            "strategy": inp.strategy_id,
            "version": inp.strategy_version,
            "config": inp.approved_strategy_config_identity,
            "position": inp.position_id.value,
            "pair": inp.pair.symbol,
            "side": inp.existing_position_side.value,
            "context": inp.evidence_context.identity_payload,
            "evaluated_at": inp.evaluated_at.isoformat(),
        },
        "capacity": cap.identity_payload,
        "signal_resolution": res.identity_payload,
        "swap_resolution": swap.identity_payload,
        "allocation": {
            "version": allocation.policy_version,
            "target_fraction": str(allocation.target_fraction),
        },
        "risk": {
            "version": risk.policy_version,
            "maximum_capacity_age_us": int(risk.maximum_capacity_age.total_seconds() * 1_000_000),
        },
        "authority": authority.value,
    }
