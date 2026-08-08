from __future__ import annotations

import decimal
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
)
from enum import StrEnum
from typing import cast

from fx_core import CurrencyPair
from fx_core.time import require_utc

from ..adoption import digest
from ..execution_authority import ExecutionAuthorityMode
from ..models import (
    ApprovedExecutionIntent,
    ApprovedLiquidationIntent,
    CandidateId,
    ExecutionIntentId,
    PositionId,
    RiskDecisionId,
    Side,
)
from ..strategy.ordinary_close import ApprovedCloseIntent

PAPER_MARKET_OBSERVATION_CONTRACT_VERSION = "paper-market-observation-v1"
PAPER_FILL_POLICY_CONTRACT_VERSION = "paper-fill-policy-v1"
PAPER_ORDER_CONTRACT_VERSION = "paper-order-v1"
FILL_EVALUATION_PLAN_CONTRACT_VERSION = "fill-evaluation-plan-v1"
FILL_EVALUATION_STEP_CONTRACT_VERSION = "fill-evaluation-step-v1"
PAPER_FILL_CONTRACT_VERSION = "paper-fill-v1"
PAPER_ATTEMPT_DISPOSITION_PENDING_NO_ELIGIBLE_MARKET = "PENDING_NO_ELIGIBLE_MARKET"
NO_MARKET_TERMINAL_REASON_CODE = "REJECTED_NO_MARKET_EVIDENCE"

# Frozen Decimal arithmetic contexts (see docs/phases/M3/spec.md "Frozen Decimal
# arithmetic"). Every M3 numeric formula names exactly one of these.
PAPER_EXACT_ARITHMETIC_V1 = Context(
    prec=50,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow, Inexact],
)
PAPER_QUOTIENT_ARITHMETIC_V1 = Context(
    prec=34,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)

class PaperIntentKind(StrEnum):
    ENTRY = "ENTRY"
    ORDINARY_CLOSE = "ORDINARY_CLOSE"
    EMERGENCY_LIQUIDATION = "EMERGENCY_LIQUIDATION"


class PaperPartialFillMode(StrEnum):
    FULL_REMAINING = "FULL_REMAINING"
    FRACTION_OF_REMAINING = "FRACTION_OF_REMAINING"


class PaperOrderState(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


_NO_FILL_TERMINAL_STATES = frozenset(
    {PaperOrderState.REJECTED, PaperOrderState.CANCELLED, PaperOrderState.EXPIRED}
)
_INCOMPLETE_TERMINAL_STATES = frozenset({PaperOrderState.CANCELLED, PaperOrderState.EXPIRED})

_OPEN_NEXT_STATES = frozenset(
    {
        PaperOrderState.PARTIALLY_FILLED,
        PaperOrderState.FILLED,
        PaperOrderState.CANCELLED,
        PaperOrderState.EXPIRED,
        PaperOrderState.REJECTED,
    }
)
_PARTIALLY_FILLED_NEXT_STATES = frozenset(
    {
        PaperOrderState.PARTIALLY_FILLED,
        PaperOrderState.FILLED,
        PaperOrderState.CANCELLED,
        PaperOrderState.EXPIRED,
    }
)
_LEGAL_TRANSITIONS: dict[PaperOrderState, frozenset[PaperOrderState]] = {
    PaperOrderState.ACCEPTED: frozenset({PaperOrderState.OPEN}),
    PaperOrderState.OPEN: _OPEN_NEXT_STATES,
    PaperOrderState.PARTIALLY_FILLED: _PARTIALLY_FILLED_NEXT_STATES,
    PaperOrderState.FILLED: frozenset(),
    PaperOrderState.CANCELLED: frozenset(),
    PaperOrderState.EXPIRED: frozenset(),
    PaperOrderState.REJECTED: frozenset(),
}


class PaperAttemptDiagnosticCode(StrEnum):
    NO_OBSERVATION_FOR_PAIR = "NO_OBSERVATION_FOR_PAIR"
    ALL_OBSERVATIONS_INELIGIBLE = "ALL_OBSERVATIONS_INELIGIBLE"


class PaperStepResolutionVariant(StrEnum):
    MARKET_SELECTED = "MARKET_SELECTED"
    NO_MARKET = "NO_MARKET"


# ---------------------------------------------------------------------------
# Small intrinsic validators (established M2-D idiom: exact-type, non-blank,
# UTC-aware; never isinstance, never a bare truthiness check).
# ---------------------------------------------------------------------------


def _text(value: object, label: str) -> None:
    if type(value) is not str:
        raise TypeError(f"{label} must be exact str")
    if not value.strip():
        raise ValueError(f"{label} must not be blank")


def _utc(value: object, label: str) -> None:
    if type(value) is not datetime:
        raise TypeError(f"{label} must be exact datetime")
    require_utc(value, label)


def _positive_finite_decimal(value: object, label: str) -> None:
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        raise ValueError(f"{label} must be a positive finite Decimal")


def _nonnegative_finite_decimal(value: object, label: str) -> None:
    if type(value) is not Decimal or not value.is_finite() or value < 0:
        raise ValueError(f"{label} must be a nonnegative finite Decimal")


def _positive_timedelta(value: object, label: str) -> None:
    if type(value) is not timedelta or value <= timedelta(0):
        raise ValueError(f"{label} must be a positive exact timedelta")


def _nonnegative_int(value: object, label: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be an exact int >= 0")


def _timedelta_us(value: timedelta) -> int:
    return value // timedelta(microseconds=1)


def _exact_id_value(value: object, expected: type, label: str) -> str:
    if type(value) is not expected:
        raise TypeError(f"{label} must be exact {expected.__name__}")
    inner = getattr(value, "value")  # noqa: B009 -- attribute name is statically unknowable here
    if type(inner) is not str or not inner.strip():
        raise ValueError(f"{label}.value must be a non-blank exact str")
    return inner


def _exact_pair(value: object, label: str = "pair") -> None:
    if type(value) is not CurrencyPair:
        raise TypeError(f"{label} must be exact CurrencyPair")
    CurrencyPair.__post_init__(value)


def _exact_side(value: object, label: str = "side") -> None:
    if type(value) is not Side:
        raise TypeError(f"{label} must be exact Side")


def opposite_side(side: Side) -> Side:
    _exact_side(side, "existing_position_side")
    return Side.SELL if side is Side.BUY else Side.BUY


# ---------------------------------------------------------------------------
# Frozen source-intent payload builders (one per exact approved-intent root).
# ---------------------------------------------------------------------------


def entry_source_intent_payload(intent: ApprovedExecutionIntent) -> dict[str, object]:
    if type(intent) is not ApprovedExecutionIntent:
        raise TypeError("entry source intent must be exact ApprovedExecutionIntent")
    ApprovedExecutionIntent.__post_init__(intent)
    intent_id = _exact_id_value(intent.intent_id, ExecutionIntentId, "intent_id")
    candidate_id = _exact_id_value(intent.candidate_id, CandidateId, "candidate_id")
    risk_decision_id = _exact_id_value(intent.risk_decision_id, RiskDecisionId, "risk_decision_id")
    _exact_pair(intent.pair)
    _exact_side(intent.side)
    _positive_finite_decimal(intent.quantity, "quantity")
    _text(intent.idempotency_key, "idempotency_key")
    _utc(intent.created_at, "created_at")
    return {
        "intent_id": intent_id,
        "candidate_id": candidate_id,
        "risk_decision_id": risk_decision_id,
        "pair": intent.pair.symbol,
        "side": intent.side.value,
        "quantity": str(intent.quantity),
        "idempotency_key": intent.idempotency_key,
        "created_at": intent.created_at.isoformat(),
    }


def ordinary_close_source_intent_payload(intent: ApprovedCloseIntent) -> dict[str, object]:
    if type(intent) is not ApprovedCloseIntent:
        raise TypeError("ordinary close source intent must be exact ApprovedCloseIntent")
    ApprovedCloseIntent.__post_init__(intent)
    for value, label in (
        (intent.close_candidate_id, "close_candidate_id"),
        (intent.portfolio_decision_id, "portfolio_decision_id"),
        (intent.risk_decision_id, "risk_decision_id"),
        (intent.capacity_evidence_id, "capacity_evidence_id"),
        (intent.idempotency_key, "idempotency_key"),
    ):
        _text(value, label)
    if type(intent.position_id) is not PositionId:
        raise TypeError("position_id must be exact PositionId")
    _exact_pair(intent.pair)
    _exact_side(intent.side)
    _positive_finite_decimal(intent.quantity, "quantity")
    if type(intent.authority) is not ExecutionAuthorityMode:
        raise TypeError("authority must be exact ExecutionAuthorityMode")
    _utc(intent.created_at, "created_at")
    return {
        "close_candidate_id": intent.close_candidate_id,
        "portfolio_decision_id": intent.portfolio_decision_id,
        "risk_decision_id": intent.risk_decision_id,
        "capacity_evidence_id": intent.capacity_evidence_id,
        "position_id": intent.position_id.value,
        "pair": intent.pair.symbol,
        "side": intent.side.value,
        "quantity": str(intent.quantity),
        "authority": intent.authority.value,
        "idempotency_key": intent.idempotency_key,
        "created_at": intent.created_at.isoformat(),
    }


def emergency_liquidation_source_intent_payload(
    intent: ApprovedLiquidationIntent,
) -> dict[str, object]:
    if type(intent) is not ApprovedLiquidationIntent:
        raise TypeError(
            "emergency liquidation source intent must be exact ApprovedLiquidationIntent"
        )
    ApprovedLiquidationIntent.__post_init__(intent)
    intent_id = _exact_id_value(intent.intent_id, ExecutionIntentId, "intent_id")
    risk_decision_id = _exact_id_value(intent.risk_decision_id, RiskDecisionId, "risk_decision_id")
    if type(intent.position_id) is not PositionId:
        raise TypeError("position_id must be exact PositionId")
    _exact_pair(intent.pair)
    _positive_finite_decimal(intent.quantity, "quantity")
    _text(intent.idempotency_key, "idempotency_key")
    _utc(intent.created_at, "created_at")
    return {
        "intent_id": intent_id,
        "risk_decision_id": risk_decision_id,
        "position_id": intent.position_id.value,
        "pair": intent.pair.symbol,
        "quantity": str(intent.quantity),
        "idempotency_key": intent.idempotency_key,
        "created_at": intent.created_at.isoformat(),
    }


@dataclass(frozen=True, slots=True)
class PaperOrderIntentLineage:
    intent_kind: PaperIntentKind
    source_intent_id: str
    source_intent_idempotency_key: str
    source_intent_content_digest: str
    paper_position_id: str

    @classmethod
    def for_entry(cls, intent: ApprovedExecutionIntent) -> PaperOrderIntentLineage:
        payload = entry_source_intent_payload(intent)
        content_digest = digest(payload)
        return cls(
            PaperIntentKind.ENTRY,
            cast(str, payload["intent_id"]),
            cast(str, payload["idempotency_key"]),
            content_digest,
            "paper-position-" + content_digest,
        )

    @classmethod
    def for_ordinary_close(cls, intent: ApprovedCloseIntent) -> PaperOrderIntentLineage:
        payload = ordinary_close_source_intent_payload(intent)
        content_digest = digest(payload)
        return cls(
            PaperIntentKind.ORDINARY_CLOSE,
            cast(str, payload["idempotency_key"]),
            cast(str, payload["idempotency_key"]),
            content_digest,
            cast(str, payload["position_id"]),
        )

    @classmethod
    def for_emergency_liquidation(
        cls, intent: ApprovedLiquidationIntent, *, existing_position_side: Side
    ) -> PaperOrderIntentLineage:
        # existing_position_side is required because ApprovedLiquidationIntent
        # carries no Side; deriving the opposite Side is exercised through
        # opposite_side() rather than stored in this lineage (see spec.md
        # "Intent kind, lineage, and Paper position identity").
        opposite_side(existing_position_side)
        payload = emergency_liquidation_source_intent_payload(intent)
        content_digest = digest(payload)
        return cls(
            PaperIntentKind.EMERGENCY_LIQUIDATION,
            cast(str, payload["intent_id"]),
            cast(str, payload["idempotency_key"]),
            content_digest,
            cast(str, payload["position_id"]),
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return {
            "intent_kind": self.intent_kind.value,
            "source_intent_id": self.source_intent_id,
            "source_intent_idempotency_key": self.source_intent_idempotency_key,
            "source_intent_content_digest": self.source_intent_content_digest,
            "paper_position_id": self.paper_position_id,
        }

    def __post_init__(self) -> None:
        if type(self.intent_kind) is not PaperIntentKind:
            raise TypeError("intent_kind must be exact PaperIntentKind")
        for value, label in (
            (self.source_intent_id, "source_intent_id"),
            (self.source_intent_idempotency_key, "source_intent_idempotency_key"),
            (self.source_intent_content_digest, "source_intent_content_digest"),
            (self.paper_position_id, "paper_position_id"),
        ):
            _text(value, label)
        if self.intent_kind is PaperIntentKind.ENTRY:
            expected_position_id = "paper-position-" + self.source_intent_content_digest
            if self.paper_position_id != expected_position_id:
                raise ValueError("ENTRY paper_position_id must derive from its content digest")
        if self.intent_kind is PaperIntentKind.ORDINARY_CLOSE:
            if self.source_intent_id != self.source_intent_idempotency_key:
                raise ValueError("ORDINARY_CLOSE source_intent_id must be its idempotency_key")


@dataclass(frozen=True, slots=True)
class PaperMarketObservation:
    market_observation_id: str
    observation_contract_version: str
    pair: CurrencyPair
    bid: Decimal
    ask: Decimal
    provider_observed_at: datetime
    received_at: datetime
    source: str
    source_version: str

    @classmethod
    def create(
        cls,
        *,
        pair: CurrencyPair,
        bid: Decimal,
        ask: Decimal,
        provider_observed_at: datetime,
        received_at: datetime,
        source: str,
        source_version: str,
    ) -> PaperMarketObservation:
        payload = _market_observation_payload(
            pair=pair,
            bid=bid,
            ask=ask,
            provider_observed_at=provider_observed_at,
            received_at=received_at,
            source=source,
            source_version=source_version,
        )
        return cls(
            "paper-market-" + digest(payload),
            PAPER_MARKET_OBSERVATION_CONTRACT_VERSION,
            pair,
            bid,
            ask,
            provider_observed_at,
            received_at,
            source,
            source_version,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _market_observation_payload(
            pair=self.pair,
            bid=self.bid,
            ask=self.ask,
            provider_observed_at=self.provider_observed_at,
            received_at=self.received_at,
            source=self.source,
            source_version=self.source_version,
        )

    def __post_init__(self) -> None:
        if type(self.market_observation_id) is not str:
            raise TypeError("market_observation_id must be exact str")
        if type(self.observation_contract_version) is not str:
            raise TypeError("observation_contract_version must be exact str")
        if self.observation_contract_version != PAPER_MARKET_OBSERVATION_CONTRACT_VERSION:
            raise ValueError("unsupported market observation contract")
        _exact_pair(self.pair)
        _positive_finite_decimal(self.bid, "bid")
        _positive_finite_decimal(self.ask, "ask")
        if self.bid > self.ask:
            raise ValueError("bid must not exceed ask")
        _utc(self.provider_observed_at, "provider_observed_at")
        _utc(self.received_at, "received_at")
        if self.provider_observed_at > self.received_at:
            raise ValueError("provider_observed_at must not be after received_at")
        _text(self.source, "source")
        _text(self.source_version, "source_version")
        expected_id = "paper-market-" + digest(self.identity_payload)
        if self.market_observation_id != expected_id:
            raise ValueError("market_observation_id does not match content")


@dataclass(frozen=True, slots=True)
class PaperFillPolicy:
    paper_fill_policy_id: str
    policy_contract_version: str
    policy_version: str
    market_selection_policy_version: str
    fill_model_version: str
    step_schedule_policy_version: str
    maximum_market_age: timedelta
    step_window_duration: timedelta
    step_gap: timedelta
    maximum_steps: int
    partial_fill_mode: PaperPartialFillMode
    partial_fill_fraction: Decimal | None
    slippage_basis_points: Decimal
    no_fill_terminal_order_state: PaperOrderState
    incomplete_terminal_order_state: PaperOrderState

    @classmethod
    def create(
        cls,
        *,
        policy_version: str,
        market_selection_policy_version: str,
        fill_model_version: str,
        step_schedule_policy_version: str,
        maximum_market_age: timedelta,
        step_window_duration: timedelta,
        step_gap: timedelta,
        maximum_steps: int,
        partial_fill_mode: PaperPartialFillMode,
        partial_fill_fraction: Decimal | None,
        slippage_basis_points: Decimal,
        no_fill_terminal_order_state: PaperOrderState,
        incomplete_terminal_order_state: PaperOrderState,
    ) -> PaperFillPolicy:
        payload = _fill_policy_payload(
            policy_version=policy_version,
            market_selection_policy_version=market_selection_policy_version,
            fill_model_version=fill_model_version,
            step_schedule_policy_version=step_schedule_policy_version,
            maximum_market_age=maximum_market_age,
            step_window_duration=step_window_duration,
            step_gap=step_gap,
            maximum_steps=maximum_steps,
            partial_fill_mode=partial_fill_mode,
            partial_fill_fraction=partial_fill_fraction,
            slippage_basis_points=slippage_basis_points,
            no_fill_terminal_order_state=no_fill_terminal_order_state,
            incomplete_terminal_order_state=incomplete_terminal_order_state,
        )
        return cls(
            "paper-fill-policy-" + digest(payload),
            PAPER_FILL_POLICY_CONTRACT_VERSION,
            policy_version,
            market_selection_policy_version,
            fill_model_version,
            step_schedule_policy_version,
            maximum_market_age,
            step_window_duration,
            step_gap,
            maximum_steps,
            partial_fill_mode,
            partial_fill_fraction,
            slippage_basis_points,
            no_fill_terminal_order_state,
            incomplete_terminal_order_state,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _fill_policy_payload(
            policy_version=self.policy_version,
            market_selection_policy_version=self.market_selection_policy_version,
            fill_model_version=self.fill_model_version,
            step_schedule_policy_version=self.step_schedule_policy_version,
            maximum_market_age=self.maximum_market_age,
            step_window_duration=self.step_window_duration,
            step_gap=self.step_gap,
            maximum_steps=self.maximum_steps,
            partial_fill_mode=self.partial_fill_mode,
            partial_fill_fraction=self.partial_fill_fraction,
            slippage_basis_points=self.slippage_basis_points,
            no_fill_terminal_order_state=self.no_fill_terminal_order_state,
            incomplete_terminal_order_state=self.incomplete_terminal_order_state,
        )

    def __post_init__(self) -> None:
        if type(self.paper_fill_policy_id) is not str:
            raise TypeError("paper_fill_policy_id must be exact str")
        if type(self.policy_contract_version) is not str:
            raise TypeError("policy_contract_version must be exact str")
        if self.policy_contract_version != PAPER_FILL_POLICY_CONTRACT_VERSION:
            raise ValueError("unsupported fill policy contract")
        for value, label in (
            (self.policy_version, "policy_version"),
            (self.market_selection_policy_version, "market_selection_policy_version"),
            (self.fill_model_version, "fill_model_version"),
            (self.step_schedule_policy_version, "step_schedule_policy_version"),
        ):
            _text(value, label)
        for duration, duration_label in (
            (self.maximum_market_age, "maximum_market_age"),
            (self.step_window_duration, "step_window_duration"),
            (self.step_gap, "step_gap"),
        ):
            _positive_timedelta(duration, duration_label)
        if (
            type(self.maximum_steps) is not int
            or isinstance(self.maximum_steps, bool)
            or self.maximum_steps < 1
        ):
            raise ValueError("maximum_steps must be an exact int >= 1")
        if type(self.partial_fill_mode) is not PaperPartialFillMode:
            raise TypeError("partial_fill_mode must be exact PaperPartialFillMode")
        if self.partial_fill_mode is PaperPartialFillMode.FRACTION_OF_REMAINING:
            if (
                type(self.partial_fill_fraction) is not Decimal
                or not self.partial_fill_fraction.is_finite()
                or not (Decimal(0) < self.partial_fill_fraction <= Decimal(1))
            ):
                raise ValueError(
                    "FRACTION_OF_REMAINING requires partial_fill_fraction in (0, 1]"
                )
        elif self.partial_fill_fraction is not None:
            raise ValueError("FULL_REMAINING must not carry a partial_fill_fraction")
        _nonnegative_finite_decimal(self.slippage_basis_points, "slippage_basis_points")
        if (
            type(self.no_fill_terminal_order_state) is not PaperOrderState
            or self.no_fill_terminal_order_state not in _NO_FILL_TERMINAL_STATES
        ):
            raise ValueError("no_fill_terminal_order_state must be REJECTED, CANCELLED, or EXPIRED")
        if (
            type(self.incomplete_terminal_order_state) is not PaperOrderState
            or self.incomplete_terminal_order_state not in _INCOMPLETE_TERMINAL_STATES
        ):
            raise ValueError("incomplete_terminal_order_state must be CANCELLED or EXPIRED")
        expected_id = "paper-fill-policy-" + digest(self.identity_payload)
        if self.paper_fill_policy_id != expected_id:
            raise ValueError("paper_fill_policy_id does not match content")


@dataclass(frozen=True, slots=True)
class PaperOrder:
    paper_order_id: str
    order_contract_version: str
    paper_account_id: str
    intent_lineage: PaperOrderIntentLineage
    pair: CurrencyPair
    side: Side
    original_quantity: Decimal
    authority: ExecutionAuthorityMode
    fill_policy_id: str
    intent_created_at: datetime
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        paper_account_id: str,
        intent_lineage: PaperOrderIntentLineage,
        pair: CurrencyPair,
        side: Side,
        original_quantity: Decimal,
        authority: ExecutionAuthorityMode,
        fill_policy_id: str,
        intent_created_at: datetime,
        created_at: datetime,
    ) -> PaperOrder:
        payload = _order_payload(
            paper_account_id=paper_account_id,
            intent_lineage=intent_lineage,
            pair=pair,
            side=side,
            original_quantity=original_quantity,
            authority=authority,
            fill_policy_id=fill_policy_id,
            intent_created_at=intent_created_at,
        )
        return cls(
            "paper-order-" + digest(payload),
            PAPER_ORDER_CONTRACT_VERSION,
            paper_account_id,
            intent_lineage,
            pair,
            side,
            original_quantity,
            authority,
            fill_policy_id,
            intent_created_at,
            created_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _order_payload(
            paper_account_id=self.paper_account_id,
            intent_lineage=self.intent_lineage,
            pair=self.pair,
            side=self.side,
            original_quantity=self.original_quantity,
            authority=self.authority,
            fill_policy_id=self.fill_policy_id,
            intent_created_at=self.intent_created_at,
        )

    def __post_init__(self) -> None:
        if type(self.paper_order_id) is not str:
            raise TypeError("paper_order_id must be exact str")
        if type(self.order_contract_version) is not str:
            raise TypeError("order_contract_version must be exact str")
        if self.order_contract_version != PAPER_ORDER_CONTRACT_VERSION:
            raise ValueError("unsupported order contract")
        _text(self.paper_account_id, "paper_account_id")
        if type(self.intent_lineage) is not PaperOrderIntentLineage:
            raise TypeError("intent_lineage must be exact PaperOrderIntentLineage")
        PaperOrderIntentLineage.__post_init__(self.intent_lineage)
        _exact_pair(self.pair)
        _exact_side(self.side)
        _positive_finite_decimal(self.original_quantity, "original_quantity")
        if type(self.authority) is not ExecutionAuthorityMode:
            raise TypeError("authority must be exact ExecutionAuthorityMode")
        if self.authority is not ExecutionAuthorityMode.PAPER:
            raise ValueError("PaperOrder authority must be PAPER")
        _text(self.fill_policy_id, "fill_policy_id")
        _utc(self.intent_created_at, "intent_created_at")
        _utc(self.created_at, "created_at")
        expected_id = "paper-order-" + digest(self.identity_payload)
        if self.paper_order_id != expected_id:
            raise ValueError("paper_order_id does not match content")


@dataclass(frozen=True, slots=True)
class PaperOrderEvent:
    paper_order_event_id: str
    paper_order_id: str
    event_ordinal: int
    state: PaperOrderState
    source_evidence_kind: str
    source_evidence_id: str | None
    appended_at: datetime

    @classmethod
    def create(
        cls,
        *,
        paper_order_id: str,
        event_ordinal: int,
        state: PaperOrderState,
        source_evidence_kind: str,
        source_evidence_id: str | None,
        appended_at: datetime,
    ) -> PaperOrderEvent:
        payload = _order_event_payload(
            paper_order_id=paper_order_id,
            event_ordinal=event_ordinal,
            state=state,
            source_evidence_kind=source_evidence_kind,
            source_evidence_id=source_evidence_id,
        )
        return cls(
            "paper-order-event-" + digest(payload),
            paper_order_id,
            event_ordinal,
            state,
            source_evidence_kind,
            source_evidence_id,
            appended_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _order_event_payload(
            paper_order_id=self.paper_order_id,
            event_ordinal=self.event_ordinal,
            state=self.state,
            source_evidence_kind=self.source_evidence_kind,
            source_evidence_id=self.source_evidence_id,
        )

    def __post_init__(self) -> None:
        if type(self.paper_order_event_id) is not str:
            raise TypeError("paper_order_event_id must be exact str")
        _text(self.paper_order_id, "paper_order_id")
        _nonnegative_int(self.event_ordinal, "event_ordinal")
        if type(self.state) is not PaperOrderState:
            raise TypeError("state must be exact PaperOrderState")
        _text(self.source_evidence_kind, "source_evidence_kind")
        if self.source_evidence_id is not None:
            _text(self.source_evidence_id, "source_evidence_id")
        _utc(self.appended_at, "appended_at")
        expected_id = "paper-order-event-" + digest(self.identity_payload)
        if self.paper_order_event_id != expected_id:
            raise ValueError("paper_order_event_id does not match content")


def require_legal_transition(previous: PaperOrderState | None, next_state: PaperOrderState) -> None:
    if previous is not None and type(previous) is not PaperOrderState:
        raise TypeError("previous state must be exact PaperOrderState or None")
    if type(next_state) is not PaperOrderState:
        raise TypeError("next_state must be exact PaperOrderState")
    if previous is None:
        if next_state is not PaperOrderState.ACCEPTED:
            raise ValueError("ordinal 0 must carry state ACCEPTED")
        return
    if next_state not in _LEGAL_TRANSITIONS[previous]:
        raise ValueError(f"illegal order transition {previous.value} -> {next_state.value}")


def project_paper_order_state(events: Sequence[PaperOrderEvent]) -> PaperOrderState:
    ordered = tuple(events)
    if not ordered:
        raise ValueError("project_paper_order_state requires at least one event")
    seen_ordinals: set[int] = set()
    paper_order_id: str | None = None
    for event in ordered:
        if type(event) is not PaperOrderEvent:
            raise TypeError("events must be exact PaperOrderEvent")
        if paper_order_id is None:
            paper_order_id = event.paper_order_id
        elif event.paper_order_id != paper_order_id:
            raise ValueError("events must all belong to the same paper_order_id")
        if event.event_ordinal in seen_ordinals:
            raise ValueError(f"duplicate event_ordinal {event.event_ordinal}")
        seen_ordinals.add(event.event_ordinal)
    if seen_ordinals != set(range(len(ordered))):
        raise ValueError("event ordinals must be contiguous starting at 0")
    previous: PaperOrderState | None = None
    for event in sorted(ordered, key=lambda item: item.event_ordinal):
        require_legal_transition(previous, event.state)
        previous = event.state
    assert previous is not None
    return previous


@dataclass(frozen=True, slots=True)
class FillEvaluationPlan:
    fill_evaluation_plan_id: str
    plan_contract_version: str
    paper_order_id: str
    intent_lineage: PaperOrderIntentLineage
    pair: CurrencyPair
    side: Side
    original_quantity: Decimal
    fill_policy_id: str
    intent_created_at: datetime
    maximum_steps: int
    plan_expiry_at: datetime
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        paper_order_id: str,
        intent_lineage: PaperOrderIntentLineage,
        pair: CurrencyPair,
        side: Side,
        original_quantity: Decimal,
        fill_policy_id: str,
        intent_created_at: datetime,
        maximum_steps: int,
        plan_expiry_at: datetime,
        created_at: datetime,
    ) -> FillEvaluationPlan:
        payload = _plan_payload(
            paper_order_id=paper_order_id,
            intent_lineage=intent_lineage,
            pair=pair,
            side=side,
            original_quantity=original_quantity,
            fill_policy_id=fill_policy_id,
            intent_created_at=intent_created_at,
            maximum_steps=maximum_steps,
            plan_expiry_at=plan_expiry_at,
        )
        return cls(
            "fill-evaluation-plan-" + digest(payload),
            FILL_EVALUATION_PLAN_CONTRACT_VERSION,
            paper_order_id,
            intent_lineage,
            pair,
            side,
            original_quantity,
            fill_policy_id,
            intent_created_at,
            maximum_steps,
            plan_expiry_at,
            created_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _plan_payload(
            paper_order_id=self.paper_order_id,
            intent_lineage=self.intent_lineage,
            pair=self.pair,
            side=self.side,
            original_quantity=self.original_quantity,
            fill_policy_id=self.fill_policy_id,
            intent_created_at=self.intent_created_at,
            maximum_steps=self.maximum_steps,
            plan_expiry_at=self.plan_expiry_at,
        )

    def __post_init__(self) -> None:
        if type(self.fill_evaluation_plan_id) is not str:
            raise TypeError("fill_evaluation_plan_id must be exact str")
        if type(self.plan_contract_version) is not str:
            raise TypeError("plan_contract_version must be exact str")
        if self.plan_contract_version != FILL_EVALUATION_PLAN_CONTRACT_VERSION:
            raise ValueError("unsupported plan contract")
        _text(self.paper_order_id, "paper_order_id")
        if type(self.intent_lineage) is not PaperOrderIntentLineage:
            raise TypeError("intent_lineage must be exact PaperOrderIntentLineage")
        PaperOrderIntentLineage.__post_init__(self.intent_lineage)
        _exact_pair(self.pair)
        _exact_side(self.side)
        _positive_finite_decimal(self.original_quantity, "original_quantity")
        _text(self.fill_policy_id, "fill_policy_id")
        _utc(self.intent_created_at, "intent_created_at")
        if (
            type(self.maximum_steps) is not int
            or isinstance(self.maximum_steps, bool)
            or self.maximum_steps < 1
        ):
            raise ValueError("maximum_steps must be an exact int >= 1")
        _utc(self.plan_expiry_at, "plan_expiry_at")
        if self.plan_expiry_at < self.intent_created_at:
            raise ValueError("plan_expiry_at must not precede intent_created_at")
        _utc(self.created_at, "created_at")
        expected_id = "fill-evaluation-plan-" + digest(self.identity_payload)
        if self.fill_evaluation_plan_id != expected_id:
            raise ValueError("fill_evaluation_plan_id does not match content")


@dataclass(frozen=True, slots=True)
class FillEvaluationStep:
    fill_evaluation_step_id: str
    step_contract_version: str
    fill_evaluation_plan_id: str
    ordinal: int
    evaluation_window_start_at: datetime
    evaluation_due_at: datetime
    remaining_quantity_before: Decimal
    fill_policy_id: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        fill_evaluation_plan_id: str,
        ordinal: int,
        evaluation_window_start_at: datetime,
        evaluation_due_at: datetime,
        remaining_quantity_before: Decimal,
        fill_policy_id: str,
        created_at: datetime,
    ) -> FillEvaluationStep:
        payload = _step_payload(
            fill_evaluation_plan_id=fill_evaluation_plan_id,
            ordinal=ordinal,
            evaluation_window_start_at=evaluation_window_start_at,
            evaluation_due_at=evaluation_due_at,
            remaining_quantity_before=remaining_quantity_before,
            fill_policy_id=fill_policy_id,
        )
        return cls(
            "fill-evaluation-step-" + digest(payload),
            FILL_EVALUATION_STEP_CONTRACT_VERSION,
            fill_evaluation_plan_id,
            ordinal,
            evaluation_window_start_at,
            evaluation_due_at,
            remaining_quantity_before,
            fill_policy_id,
            created_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _step_payload(
            fill_evaluation_plan_id=self.fill_evaluation_plan_id,
            ordinal=self.ordinal,
            evaluation_window_start_at=self.evaluation_window_start_at,
            evaluation_due_at=self.evaluation_due_at,
            remaining_quantity_before=self.remaining_quantity_before,
            fill_policy_id=self.fill_policy_id,
        )

    def __post_init__(self) -> None:
        if type(self.fill_evaluation_step_id) is not str:
            raise TypeError("fill_evaluation_step_id must be exact str")
        if type(self.step_contract_version) is not str:
            raise TypeError("step_contract_version must be exact str")
        if self.step_contract_version != FILL_EVALUATION_STEP_CONTRACT_VERSION:
            raise ValueError("unsupported step contract")
        _text(self.fill_evaluation_plan_id, "fill_evaluation_plan_id")
        _nonnegative_int(self.ordinal, "ordinal")
        _utc(self.evaluation_window_start_at, "evaluation_window_start_at")
        _utc(self.evaluation_due_at, "evaluation_due_at")
        if self.evaluation_due_at < self.evaluation_window_start_at:
            raise ValueError("evaluation_due_at must not precede evaluation_window_start_at")
        _positive_finite_decimal(self.remaining_quantity_before, "remaining_quantity_before")
        _text(self.fill_policy_id, "fill_policy_id")
        _utc(self.created_at, "created_at")
        expected_id = "fill-evaluation-step-" + digest(self.identity_payload)
        if self.fill_evaluation_step_id != expected_id:
            raise ValueError("fill_evaluation_step_id does not match content")


@dataclass(frozen=True, slots=True)
class FillEvaluationAttempt:
    fill_evaluation_attempt_id: str
    fill_evaluation_step_id: str
    evaluated_at: datetime
    disposition: str
    diagnostic_code: PaperAttemptDiagnosticCode
    worker_identity: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        fill_evaluation_step_id: str,
        evaluated_at: datetime,
        disposition: str,
        diagnostic_code: PaperAttemptDiagnosticCode,
        worker_identity: str,
        created_at: datetime,
    ) -> FillEvaluationAttempt:
        payload = _attempt_payload(
            fill_evaluation_step_id=fill_evaluation_step_id,
            evaluated_at=evaluated_at,
            disposition=disposition,
            diagnostic_code=diagnostic_code,
            worker_identity=worker_identity,
        )
        return cls(
            "fill-evaluation-attempt-" + digest(payload),
            fill_evaluation_step_id,
            evaluated_at,
            disposition,
            diagnostic_code,
            worker_identity,
            created_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _attempt_payload(
            fill_evaluation_step_id=self.fill_evaluation_step_id,
            evaluated_at=self.evaluated_at,
            disposition=self.disposition,
            diagnostic_code=self.diagnostic_code,
            worker_identity=self.worker_identity,
        )

    def __post_init__(self) -> None:
        if type(self.fill_evaluation_attempt_id) is not str:
            raise TypeError("fill_evaluation_attempt_id must be exact str")
        _text(self.fill_evaluation_step_id, "fill_evaluation_step_id")
        _utc(self.evaluated_at, "evaluated_at")
        if (
            type(self.disposition) is not str
            or self.disposition != PAPER_ATTEMPT_DISPOSITION_PENDING_NO_ELIGIBLE_MARKET
        ):
            raise ValueError("disposition must be exact PENDING_NO_ELIGIBLE_MARKET")
        if type(self.diagnostic_code) is not PaperAttemptDiagnosticCode:
            raise TypeError("diagnostic_code must be exact PaperAttemptDiagnosticCode")
        _text(self.worker_identity, "worker_identity")
        _utc(self.created_at, "created_at")
        expected_id = "fill-evaluation-attempt-" + digest(self.identity_payload)
        if self.fill_evaluation_attempt_id != expected_id:
            raise ValueError("fill_evaluation_attempt_id does not match content")


@dataclass(frozen=True, slots=True)
class PaperMarketObservationSelection:
    market_observation_selection_id: str
    fill_evaluation_step_id: str
    fill_evaluation_plan_id: str
    market_observation_id: str
    market_selection_policy_version: str
    evaluation_window_start_at: datetime
    evaluation_due_at: datetime
    intent_created_at: datetime
    selected_at: datetime

    @classmethod
    def create(
        cls,
        *,
        fill_evaluation_step_id: str,
        fill_evaluation_plan_id: str,
        market_observation_id: str,
        market_selection_policy_version: str,
        evaluation_window_start_at: datetime,
        evaluation_due_at: datetime,
        intent_created_at: datetime,
        selected_at: datetime,
    ) -> PaperMarketObservationSelection:
        payload = _selection_payload(
            fill_evaluation_step_id=fill_evaluation_step_id,
            fill_evaluation_plan_id=fill_evaluation_plan_id,
            market_observation_id=market_observation_id,
            market_selection_policy_version=market_selection_policy_version,
            evaluation_window_start_at=evaluation_window_start_at,
            evaluation_due_at=evaluation_due_at,
            intent_created_at=intent_created_at,
        )
        return cls(
            "market-observation-selection-" + digest(payload),
            fill_evaluation_step_id,
            fill_evaluation_plan_id,
            market_observation_id,
            market_selection_policy_version,
            evaluation_window_start_at,
            evaluation_due_at,
            intent_created_at,
            selected_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _selection_payload(
            fill_evaluation_step_id=self.fill_evaluation_step_id,
            fill_evaluation_plan_id=self.fill_evaluation_plan_id,
            market_observation_id=self.market_observation_id,
            market_selection_policy_version=self.market_selection_policy_version,
            evaluation_window_start_at=self.evaluation_window_start_at,
            evaluation_due_at=self.evaluation_due_at,
            intent_created_at=self.intent_created_at,
        )

    def __post_init__(self) -> None:
        if type(self.market_observation_selection_id) is not str:
            raise TypeError("market_observation_selection_id must be exact str")
        for value, label in (
            (self.fill_evaluation_step_id, "fill_evaluation_step_id"),
            (self.fill_evaluation_plan_id, "fill_evaluation_plan_id"),
            (self.market_observation_id, "market_observation_id"),
            (self.market_selection_policy_version, "market_selection_policy_version"),
        ):
            _text(value, label)
        _utc(self.evaluation_window_start_at, "evaluation_window_start_at")
        _utc(self.evaluation_due_at, "evaluation_due_at")
        _utc(self.intent_created_at, "intent_created_at")
        if self.evaluation_due_at < self.evaluation_window_start_at:
            raise ValueError("evaluation_due_at must not precede evaluation_window_start_at")
        if self.evaluation_window_start_at < self.intent_created_at:
            raise ValueError("evaluation_window_start_at must not precede intent_created_at")
        _utc(self.selected_at, "selected_at")
        expected_id = "market-observation-selection-" + digest(self.identity_payload)
        if self.market_observation_selection_id != expected_id:
            raise ValueError("market_observation_selection_id does not match content")


@dataclass(frozen=True, slots=True)
class PaperNoMarketOutcome:
    no_market_outcome_id: str
    fill_evaluation_step_id: str
    terminal_reason_code: str
    evaluation_due_at: datetime
    resolved_at: datetime

    @classmethod
    def create(
        cls,
        *,
        fill_evaluation_step_id: str,
        evaluation_due_at: datetime,
        resolved_at: datetime,
    ) -> PaperNoMarketOutcome:
        payload = _no_market_outcome_payload(
            fill_evaluation_step_id=fill_evaluation_step_id,
            evaluation_due_at=evaluation_due_at,
        )
        return cls(
            "no-market-outcome-" + digest(payload),
            fill_evaluation_step_id,
            NO_MARKET_TERMINAL_REASON_CODE,
            evaluation_due_at,
            resolved_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _no_market_outcome_payload(
            fill_evaluation_step_id=self.fill_evaluation_step_id,
            evaluation_due_at=self.evaluation_due_at,
        )

    def __post_init__(self) -> None:
        if type(self.no_market_outcome_id) is not str:
            raise TypeError("no_market_outcome_id must be exact str")
        _text(self.fill_evaluation_step_id, "fill_evaluation_step_id")
        if (
            type(self.terminal_reason_code) is not str
            or self.terminal_reason_code != NO_MARKET_TERMINAL_REASON_CODE
        ):
            raise ValueError("terminal_reason_code must be exact REJECTED_NO_MARKET_EVIDENCE")
        _utc(self.evaluation_due_at, "evaluation_due_at")
        _utc(self.resolved_at, "resolved_at")
        expected_id = "no-market-outcome-" + digest(self.identity_payload)
        if self.no_market_outcome_id != expected_id:
            raise ValueError("no_market_outcome_id does not match content")


@dataclass(frozen=True, slots=True)
class PaperFill:
    paper_fill_id: str
    fill_contract_version: str
    fill_evaluation_step_id: str
    market_observation_selection_id: str
    market_observation_id: str
    pair: CurrencyPair
    side: Side
    fill_quantity: Decimal
    fill_price: Decimal
    reference_price: Decimal
    slippage_basis_points: Decimal
    fill_model_version: str
    remaining_quantity_before: Decimal
    remaining_quantity_after: Decimal
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        fill_evaluation_step_id: str,
        market_observation_selection_id: str,
        market_observation_id: str,
        pair: CurrencyPair,
        side: Side,
        fill_quantity: Decimal,
        fill_price: Decimal,
        reference_price: Decimal,
        slippage_basis_points: Decimal,
        fill_model_version: str,
        remaining_quantity_before: Decimal,
        remaining_quantity_after: Decimal,
        created_at: datetime,
    ) -> PaperFill:
        payload = _fill_payload(
            fill_evaluation_step_id=fill_evaluation_step_id,
            market_observation_selection_id=market_observation_selection_id,
            market_observation_id=market_observation_id,
            pair=pair,
            side=side,
            fill_quantity=fill_quantity,
            fill_price=fill_price,
            reference_price=reference_price,
            slippage_basis_points=slippage_basis_points,
            fill_model_version=fill_model_version,
            remaining_quantity_before=remaining_quantity_before,
            remaining_quantity_after=remaining_quantity_after,
        )
        return cls(
            "paper-fill-" + digest(payload),
            PAPER_FILL_CONTRACT_VERSION,
            fill_evaluation_step_id,
            market_observation_selection_id,
            market_observation_id,
            pair,
            side,
            fill_quantity,
            fill_price,
            reference_price,
            slippage_basis_points,
            fill_model_version,
            remaining_quantity_before,
            remaining_quantity_after,
            created_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _fill_payload(
            fill_evaluation_step_id=self.fill_evaluation_step_id,
            market_observation_selection_id=self.market_observation_selection_id,
            market_observation_id=self.market_observation_id,
            pair=self.pair,
            side=self.side,
            fill_quantity=self.fill_quantity,
            fill_price=self.fill_price,
            reference_price=self.reference_price,
            slippage_basis_points=self.slippage_basis_points,
            fill_model_version=self.fill_model_version,
            remaining_quantity_before=self.remaining_quantity_before,
            remaining_quantity_after=self.remaining_quantity_after,
        )

    def __post_init__(self) -> None:
        if type(self.paper_fill_id) is not str:
            raise TypeError("paper_fill_id must be exact str")
        if type(self.fill_contract_version) is not str:
            raise TypeError("fill_contract_version must be exact str")
        if self.fill_contract_version != PAPER_FILL_CONTRACT_VERSION:
            raise ValueError("unsupported fill contract")
        for value, label in (
            (self.fill_evaluation_step_id, "fill_evaluation_step_id"),
            (self.market_observation_selection_id, "market_observation_selection_id"),
            (self.market_observation_id, "market_observation_id"),
            (self.fill_model_version, "fill_model_version"),
        ):
            _text(value, label)
        _exact_pair(self.pair)
        _exact_side(self.side)
        _positive_finite_decimal(self.fill_quantity, "fill_quantity")
        _positive_finite_decimal(self.fill_price, "fill_price")
        _positive_finite_decimal(self.reference_price, "reference_price")
        _nonnegative_finite_decimal(self.slippage_basis_points, "slippage_basis_points")
        _positive_finite_decimal(self.remaining_quantity_before, "remaining_quantity_before")
        _nonnegative_finite_decimal(self.remaining_quantity_after, "remaining_quantity_after")
        if self.fill_quantity > self.remaining_quantity_before:
            raise ValueError("fill_quantity must not exceed remaining_quantity_before")
        with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
            expected_remaining_after = self.remaining_quantity_before - self.fill_quantity
        if self.remaining_quantity_after != expected_remaining_after:
            raise ValueError("remaining_quantity_after must equal before minus fill_quantity")
        _utc(self.created_at, "created_at")
        expected_id = "paper-fill-" + digest(self.identity_payload)
        if self.paper_fill_id != expected_id:
            raise ValueError("paper_fill_id does not match content")


# ---------------------------------------------------------------------------
# Identity payload helpers (kept separate from create()/identity_payload so
# both use the exact same field set and transforms).
# ---------------------------------------------------------------------------


def _market_observation_payload(
    *,
    pair: CurrencyPair,
    bid: Decimal,
    ask: Decimal,
    provider_observed_at: datetime,
    received_at: datetime,
    source: str,
    source_version: str,
) -> dict[str, object]:
    return {
        "observation_contract_version": PAPER_MARKET_OBSERVATION_CONTRACT_VERSION,
        "pair": pair.symbol,
        "bid": str(bid),
        "ask": str(ask),
        "provider_observed_at": provider_observed_at.isoformat(),
        "received_at": received_at.isoformat(),
        "source": source,
        "source_version": source_version,
    }


def _fill_policy_payload(
    *,
    policy_version: str,
    market_selection_policy_version: str,
    fill_model_version: str,
    step_schedule_policy_version: str,
    maximum_market_age: timedelta,
    step_window_duration: timedelta,
    step_gap: timedelta,
    maximum_steps: int,
    partial_fill_mode: PaperPartialFillMode,
    partial_fill_fraction: Decimal | None,
    slippage_basis_points: Decimal,
    no_fill_terminal_order_state: PaperOrderState,
    incomplete_terminal_order_state: PaperOrderState,
) -> dict[str, object]:
    return {
        "policy_contract_version": PAPER_FILL_POLICY_CONTRACT_VERSION,
        "policy_version": policy_version,
        "market_selection_policy_version": market_selection_policy_version,
        "fill_model_version": fill_model_version,
        "step_schedule_policy_version": step_schedule_policy_version,
        "maximum_market_age_us": _timedelta_us(maximum_market_age),
        "step_window_duration_us": _timedelta_us(step_window_duration),
        "step_gap_us": _timedelta_us(step_gap),
        "maximum_steps": maximum_steps,
        "partial_fill_mode": partial_fill_mode.value,
        "partial_fill_fraction": (
            None if partial_fill_fraction is None else str(partial_fill_fraction)
        ),
        "slippage_basis_points": str(slippage_basis_points),
        "no_fill_terminal_order_state": no_fill_terminal_order_state.value,
        "incomplete_terminal_order_state": incomplete_terminal_order_state.value,
    }


def _order_payload(
    *,
    paper_account_id: str,
    intent_lineage: PaperOrderIntentLineage,
    pair: CurrencyPair,
    side: Side,
    original_quantity: Decimal,
    authority: ExecutionAuthorityMode,
    fill_policy_id: str,
    intent_created_at: datetime,
) -> dict[str, object]:
    return {
        "order_contract_version": PAPER_ORDER_CONTRACT_VERSION,
        "paper_account_id": paper_account_id,
        "intent_lineage": intent_lineage.identity_payload,
        "pair": pair.symbol,
        "side": side.value,
        "original_quantity": str(original_quantity),
        "authority": authority.value,
        "fill_policy_id": fill_policy_id,
        "intent_created_at": intent_created_at.isoformat(),
    }


def _order_event_payload(
    *,
    paper_order_id: str,
    event_ordinal: int,
    state: PaperOrderState,
    source_evidence_kind: str,
    source_evidence_id: str | None,
) -> dict[str, object]:
    return {
        "paper_order_id": paper_order_id,
        "event_ordinal": event_ordinal,
        "state": state.value,
        "source_evidence_kind": source_evidence_kind,
        "source_evidence_id": source_evidence_id,
    }


def _plan_payload(
    *,
    paper_order_id: str,
    intent_lineage: PaperOrderIntentLineage,
    pair: CurrencyPair,
    side: Side,
    original_quantity: Decimal,
    fill_policy_id: str,
    intent_created_at: datetime,
    maximum_steps: int,
    plan_expiry_at: datetime,
) -> dict[str, object]:
    return {
        "plan_contract_version": FILL_EVALUATION_PLAN_CONTRACT_VERSION,
        "paper_order_id": paper_order_id,
        "intent_lineage": intent_lineage.identity_payload,
        "pair": pair.symbol,
        "side": side.value,
        "original_quantity": str(original_quantity),
        "fill_policy_id": fill_policy_id,
        "intent_created_at": intent_created_at.isoformat(),
        "maximum_steps": maximum_steps,
        "plan_expiry_at": plan_expiry_at.isoformat(),
    }


def _step_payload(
    *,
    fill_evaluation_plan_id: str,
    ordinal: int,
    evaluation_window_start_at: datetime,
    evaluation_due_at: datetime,
    remaining_quantity_before: Decimal,
    fill_policy_id: str,
) -> dict[str, object]:
    return {
        "step_contract_version": FILL_EVALUATION_STEP_CONTRACT_VERSION,
        "fill_evaluation_plan_id": fill_evaluation_plan_id,
        "ordinal": ordinal,
        "evaluation_window_start_at": evaluation_window_start_at.isoformat(),
        "evaluation_due_at": evaluation_due_at.isoformat(),
        "remaining_quantity_before": str(remaining_quantity_before),
        "fill_policy_id": fill_policy_id,
    }


def _attempt_payload(
    *,
    fill_evaluation_step_id: str,
    evaluated_at: datetime,
    disposition: str,
    diagnostic_code: PaperAttemptDiagnosticCode,
    worker_identity: str,
) -> dict[str, object]:
    return {
        "fill_evaluation_step_id": fill_evaluation_step_id,
        "evaluated_at": evaluated_at.isoformat(),
        "disposition": disposition,
        "diagnostic_code": diagnostic_code.value,
        "worker_identity": worker_identity,
    }


def _selection_payload(
    *,
    fill_evaluation_step_id: str,
    fill_evaluation_plan_id: str,
    market_observation_id: str,
    market_selection_policy_version: str,
    evaluation_window_start_at: datetime,
    evaluation_due_at: datetime,
    intent_created_at: datetime,
) -> dict[str, object]:
    return {
        "fill_evaluation_step_id": fill_evaluation_step_id,
        "fill_evaluation_plan_id": fill_evaluation_plan_id,
        "market_observation_id": market_observation_id,
        "market_selection_policy_version": market_selection_policy_version,
        "evaluation_window_start_at": evaluation_window_start_at.isoformat(),
        "evaluation_due_at": evaluation_due_at.isoformat(),
        "intent_created_at": intent_created_at.isoformat(),
    }


def _no_market_outcome_payload(
    *, fill_evaluation_step_id: str, evaluation_due_at: datetime
) -> dict[str, object]:
    return {
        "fill_evaluation_step_id": fill_evaluation_step_id,
        "terminal_reason_code": NO_MARKET_TERMINAL_REASON_CODE,
        "evaluation_due_at": evaluation_due_at.isoformat(),
    }


def _fill_payload(
    *,
    fill_evaluation_step_id: str,
    market_observation_selection_id: str,
    market_observation_id: str,
    pair: CurrencyPair,
    side: Side,
    fill_quantity: Decimal,
    fill_price: Decimal,
    reference_price: Decimal,
    slippage_basis_points: Decimal,
    fill_model_version: str,
    remaining_quantity_before: Decimal,
    remaining_quantity_after: Decimal,
) -> dict[str, object]:
    return {
        "fill_contract_version": PAPER_FILL_CONTRACT_VERSION,
        "fill_evaluation_step_id": fill_evaluation_step_id,
        "market_observation_selection_id": market_observation_selection_id,
        "market_observation_id": market_observation_id,
        "pair": pair.symbol,
        "side": side.value,
        "fill_quantity": str(fill_quantity),
        "fill_price": str(fill_price),
        "reference_price": str(reference_price),
        "slippage_basis_points": str(slippage_basis_points),
        "fill_model_version": fill_model_version,
        "remaining_quantity_before": str(remaining_quantity_before),
        "remaining_quantity_after": str(remaining_quantity_after),
    }
