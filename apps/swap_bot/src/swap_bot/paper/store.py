from __future__ import annotations

import decimal
import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from fx_core import Currency, CurrencyPair
from fx_core.time import require_utc

from ..adoption import digest
from ..execution_authority import ExecutionAuthorityMode
from ..live_migrations import migrate_live_database
from ..models import ApprovedExecutionIntent, ApprovedLiquidationIntent, PositionId, Side
from ..strategy.ordinary_close import ApprovedCloseIntent
from ..strategy.swap_evidence import OperationalSwapEvidence
from .contracts import (
    PAPER_ATTEMPT_DISPOSITION_PENDING_NO_ELIGIBLE_MARKET,
    PAPER_EXACT_ARITHMETIC_V1,
    FillEvaluationAttempt,
    FillEvaluationPlan,
    FillEvaluationStep,
    PaperAttemptDiagnosticCode,
    PaperFill,
    PaperFillPolicy,
    PaperIntentKind,
    PaperMarketObservation,
    PaperMarketObservationSelection,
    PaperNoMarketOutcome,
    PaperOrder,
    PaperOrderEvent,
    PaperOrderIntentLineage,
    PaperOrderState,
    PaperPartialFillMode,
    PaperStepResolutionVariant,
    opposite_side,
)
from .fill_engine import (
    MarketSelectedStepEvaluation,
    NoMarketStepEvaluation,
    PendingStepEvaluation,
    assess_observation_eligibility,
    evaluate_fill_evaluation_step,
    is_next_step_legitimate,
)
from .ledger import (
    PAPER_REALIZED_PNL_V1,
    PAPER_SWAP_ACCRUAL_V1,
    PaperAccountBootstrap,
    PaperAccountMarkSet,
    PaperAccountSnapshot,
    PaperAccountSnapshotPositionInput,
    PaperLedgerEntry,
    PaperLedgerEntryKind,
    PaperPositionApplicationKind,
    PaperPositionFillApplication,
    PaperPositionSide,
    PaperPositionSnapshot,
    PaperReconciledRecordKind,
    PaperReconciliationResult,
    PaperSwapAccrual,
    PaperSwapAccrualCorrection,
    PaperSwapAccrualOutcome,
    PaperSwapAccrualPolicy,
    PaperSwapNonAccrual,
    compute_position_application_fields,
    evaluate_paper_swap_accrual,
    next_swap_accrual_correction,
    paper_account_equity_v1,
    paper_account_mark_set_required_coverage_v1,
    paper_available_margin_v1,
    paper_gross_exposure_v1,
    paper_open_order_count_v1,
    paper_open_position_count_v1,
    paper_unrealized_pnl_v1,
    paper_used_margin_v1,
    paper_weighted_average_entry_price_v1,
    project_paper_position_open_quantity,
    rebuild_account_snapshot,
    rebuild_ledger_entries,
    rebuild_position_fill_applications,
    rebuild_position_snapshot,
)


class PaperPersistenceConflict(ValueError):
    """A content-addressed row already exists with different content."""


class PaperPersistenceIntegrityError(ValueError):
    """A missing/corrupted parent, a failed authentication, or a frozen time/reservation rule."""


_JPY = Currency("JPY")
_PAPER_POSITION_CONTRACT_VERSION = "paper-position-v1"
_RESERVATION_CONSUMPTION_CONTRACT_VERSION = "paper-reservation-consumption-v1"
_RESERVATION_RELEASE_CONTRACT_VERSION = "paper-reservation-release-v1"


def _require_utc_datetime(value: object, label: str) -> None:
    if type(value) is not datetime:
        raise TypeError(f"{label} must be exact datetime")
    require_utc(value, label)


def _dt(value: datetime) -> str:
    return value.isoformat()


def _timedelta_us(value: timedelta) -> int:
    return value // timedelta(microseconds=1)


def _scalar(connection: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> object:
    row = connection.execute(sql, params).fetchone()
    return None if row is None else row[0]


def _scalar_int(connection: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> int:
    value = _scalar(connection, sql, params)
    return 0 if value is None else int(value)  # type: ignore[call-overload]


# ---------------------------------------------------------------------------
# Result / evidence types owned by B4
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PaperPositionRecord:
    paper_position_id: str
    position_contract_version: str
    paper_account_id: str
    entry_paper_order_id: str
    pair: CurrencyPair
    position_side: PaperPositionSide
    created_at: datetime

    def __post_init__(self) -> None:
        for value, label in (
            (self.paper_position_id, "paper_position_id"),
            (self.paper_account_id, "paper_account_id"),
            (self.entry_paper_order_id, "entry_paper_order_id"),
        ):
            if type(value) is not str or not value.strip():
                raise ValueError(f"{label} must be a non-blank exact str")
        if self.position_contract_version != _PAPER_POSITION_CONTRACT_VERSION:
            raise ValueError("unsupported position contract")
        if type(self.pair) is not CurrencyPair:
            raise TypeError("pair must be exact CurrencyPair")
        if type(self.position_side) is not PaperPositionSide:
            raise TypeError("position_side must be exact PaperPositionSide")
        _require_utc_datetime(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class ReservationConsumptionEvidence:
    consumption_id: str
    contract_version: str
    close_intent_idempotency_key: str
    paper_order_id: str
    paper_fill_id: str
    consumed_quantity: Decimal
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        close_intent_idempotency_key: str,
        paper_order_id: str,
        paper_fill_id: str,
        consumed_quantity: Decimal,
        created_at: datetime,
    ) -> ReservationConsumptionEvidence:
        payload = _consumption_payload(
            close_intent_idempotency_key=close_intent_idempotency_key,
            paper_order_id=paper_order_id,
            paper_fill_id=paper_fill_id,
            consumed_quantity=consumed_quantity,
        )
        return cls(
            "paper-reservation-consumption-" + digest(payload),
            _RESERVATION_CONSUMPTION_CONTRACT_VERSION,
            close_intent_idempotency_key,
            paper_order_id,
            paper_fill_id,
            consumed_quantity,
            created_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _consumption_payload(
            close_intent_idempotency_key=self.close_intent_idempotency_key,
            paper_order_id=self.paper_order_id,
            paper_fill_id=self.paper_fill_id,
            consumed_quantity=self.consumed_quantity,
        )

    def __post_init__(self) -> None:
        if type(self.consumption_id) is not str:
            raise TypeError("consumption_id must be exact str")
        if self.contract_version != _RESERVATION_CONSUMPTION_CONTRACT_VERSION:
            raise ValueError("unsupported reservation consumption contract")
        for value, label in (
            (self.close_intent_idempotency_key, "close_intent_idempotency_key"),
            (self.paper_order_id, "paper_order_id"),
            (self.paper_fill_id, "paper_fill_id"),
        ):
            if type(value) is not str or not value.strip():
                raise ValueError(f"{label} must be a non-blank exact str")
        if (
            type(self.consumed_quantity) is not Decimal
            or not self.consumed_quantity.is_finite()
            or self.consumed_quantity <= 0
        ):
            raise ValueError("consumed_quantity must be a positive finite Decimal")
        _require_utc_datetime(self.created_at, "created_at")
        expected_id = "paper-reservation-consumption-" + digest(self.identity_payload)
        if self.consumption_id != expected_id:
            raise ValueError("consumption_id does not match content")


def _consumption_payload(
    *,
    close_intent_idempotency_key: str,
    paper_order_id: str,
    paper_fill_id: str,
    consumed_quantity: Decimal,
) -> dict[str, object]:
    return {
        "contract_version": _RESERVATION_CONSUMPTION_CONTRACT_VERSION,
        "close_intent_idempotency_key": close_intent_idempotency_key,
        "paper_order_id": paper_order_id,
        "paper_fill_id": paper_fill_id,
        "consumed_quantity": str(consumed_quantity),
    }


@dataclass(frozen=True, slots=True)
class ReservationReleaseEvidence:
    release_id: str
    contract_version: str
    close_intent_idempotency_key: str
    paper_order_id: str
    terminal_order_state: PaperOrderState
    released_quantity: Decimal
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        close_intent_idempotency_key: str,
        paper_order_id: str,
        terminal_order_state: PaperOrderState,
        released_quantity: Decimal,
        created_at: datetime,
    ) -> ReservationReleaseEvidence:
        payload = _release_payload(
            close_intent_idempotency_key=close_intent_idempotency_key,
            paper_order_id=paper_order_id,
            terminal_order_state=terminal_order_state,
            released_quantity=released_quantity,
        )
        return cls(
            "paper-reservation-release-" + digest(payload),
            _RESERVATION_RELEASE_CONTRACT_VERSION,
            close_intent_idempotency_key,
            paper_order_id,
            terminal_order_state,
            released_quantity,
            created_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _release_payload(
            close_intent_idempotency_key=self.close_intent_idempotency_key,
            paper_order_id=self.paper_order_id,
            terminal_order_state=self.terminal_order_state,
            released_quantity=self.released_quantity,
        )

    def __post_init__(self) -> None:
        if type(self.release_id) is not str:
            raise TypeError("release_id must be exact str")
        if self.contract_version != _RESERVATION_RELEASE_CONTRACT_VERSION:
            raise ValueError("unsupported reservation release contract")
        for value, label in (
            (self.close_intent_idempotency_key, "close_intent_idempotency_key"),
            (self.paper_order_id, "paper_order_id"),
        ):
            if type(value) is not str or not value.strip():
                raise ValueError(f"{label} must be a non-blank exact str")
        if type(
            self.terminal_order_state
        ) is not PaperOrderState or self.terminal_order_state not in (
            PaperOrderState.CANCELLED,
            PaperOrderState.EXPIRED,
            PaperOrderState.REJECTED,
        ):
            raise ValueError("terminal_order_state must be CANCELLED, EXPIRED, or REJECTED")
        if (
            type(self.released_quantity) is not Decimal
            or not self.released_quantity.is_finite()
            or self.released_quantity <= 0
        ):
            raise ValueError("released_quantity must be a positive finite Decimal")
        _require_utc_datetime(self.created_at, "created_at")
        expected_id = "paper-reservation-release-" + digest(self.identity_payload)
        if self.release_id != expected_id:
            raise ValueError("release_id does not match content")


def _release_payload(
    *,
    close_intent_idempotency_key: str,
    paper_order_id: str,
    terminal_order_state: PaperOrderState,
    released_quantity: Decimal,
) -> dict[str, object]:
    return {
        "contract_version": _RESERVATION_RELEASE_CONTRACT_VERSION,
        "close_intent_idempotency_key": close_intent_idempotency_key,
        "paper_order_id": paper_order_id,
        "terminal_order_state": terminal_order_state.value,
        "released_quantity": str(released_quantity),
    }


@dataclass(frozen=True, slots=True)
class AcceptedOrder:
    order: PaperOrder
    accepted_event: PaperOrderEvent
    plan: FillEvaluationPlan


@dataclass(frozen=True, slots=True)
class CreatedStep:
    step: FillEvaluationStep
    open_event: PaperOrderEvent | None


class StepResolutionOutcome(StrEnum):
    PENDING = "PENDING"
    T3A = "T3A"
    T3B = "T3B"
    T3C = "T3C"


@dataclass(frozen=True, slots=True)
class EvaluatedStep:
    outcome: StepResolutionOutcome
    order_events: tuple[PaperOrderEvent, ...]
    attempt: FillEvaluationAttempt | None
    selection: PaperMarketObservationSelection | None
    fill: PaperFill | None
    no_market_outcome: PaperNoMarketOutcome | None
    position_fill_application: PaperPositionFillApplication | None
    ledger_entry: PaperLedgerEntry | None
    position_snapshot: PaperPositionSnapshot | None
    account_snapshot: PaperAccountSnapshot | None
    reservation_consumption: ReservationConsumptionEvidence | None
    reservation_release: ReservationReleaseEvidence | None


@dataclass(frozen=True, slots=True)
class SwapRolloverResult:
    outcome: PaperSwapAccrualOutcome
    accrual: PaperSwapAccrual | None
    non_accrual: PaperSwapNonAccrual | None
    ledger_entry: PaperLedgerEntry | None
    position_snapshot: PaperPositionSnapshot | None
    account_snapshot: PaperAccountSnapshot | None


@dataclass(frozen=True, slots=True)
class SwapCorrectionResult:
    correction: PaperSwapAccrualCorrection
    ledger_entry: PaperLedgerEntry
    position_snapshot: PaperPositionSnapshot
    account_snapshot: PaperAccountSnapshot


@dataclass(frozen=True, slots=True)
class _ReduceOnlyAttach:
    paper_position_id: str
    expected_position_side: PaperPositionSide


def _position_side_for(side: Side) -> PaperPositionSide:
    return PaperPositionSide.LONG if side is Side.BUY else PaperPositionSide.SHORT


# ---------------------------------------------------------------------------
# Generic append-or-compare (never trusts INSERT OR IGNORE's own rowcount)
# ---------------------------------------------------------------------------

_Row = tuple[tuple[str, ...], tuple[object, ...]]


def _insert_or_compare(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    values: tuple[object, ...],
    key_columns: tuple[str, ...],
    conflict_message: str,
) -> None:
    placeholders = ", ".join("?" for _ in columns)
    connection.execute(
        f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    where = " AND ".join(f"{column} = ?" for column in key_columns)
    key_values = tuple(values[columns.index(column)] for column in key_columns)
    row = connection.execute(
        f"SELECT {', '.join(columns)} FROM {table} WHERE {where}", key_values
    ).fetchone()
    if row is None or tuple(row) != values:
        raise PaperPersistenceConflict(conflict_message)


def _insert_or_compare_returning_seq(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    values: tuple[object, ...],
    key_columns: tuple[str, ...],
    conflict_message: str,
    *,
    seq_column: str,
) -> int:
    placeholders = ", ".join("?" for _ in columns)
    connection.execute(
        f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    where = " AND ".join(f"{column} = ?" for column in key_columns)
    key_values = tuple(values[columns.index(column)] for column in key_columns)
    row = connection.execute(
        f"SELECT {seq_column}, {', '.join(columns)} FROM {table} WHERE {where}", key_values
    ).fetchone()
    if row is None or tuple(row)[1:] != values:
        raise PaperPersistenceConflict(conflict_message)
    return int(row[0])


# ---------------------------------------------------------------------------
# Per-entity (columns, values) serializers and row hydrators
# ---------------------------------------------------------------------------


def _market_observation_row(o: PaperMarketObservation) -> _Row:
    columns = (
        "market_observation_id",
        "observation_contract_version",
        "pair",
        "bid",
        "ask",
        "provider_observed_at",
        "received_at",
        "source",
        "source_version",
    )
    values = (
        o.market_observation_id,
        o.observation_contract_version,
        o.pair.symbol,
        str(o.bid),
        str(o.ask),
        _dt(o.provider_observed_at),
        _dt(o.received_at),
        o.source,
        o.source_version,
    )
    return columns, values


def _hydrate_market_observation(row: sqlite3.Row) -> PaperMarketObservation:
    return PaperMarketObservation(
        row["market_observation_id"],
        row["observation_contract_version"],
        CurrencyPair.parse(row["pair"]),
        Decimal(row["bid"]),
        Decimal(row["ask"]),
        datetime.fromisoformat(row["provider_observed_at"]),
        datetime.fromisoformat(row["received_at"]),
        row["source"],
        row["source_version"],
    )


def _fill_policy_row(p: PaperFillPolicy) -> _Row:
    columns = (
        "paper_fill_policy_id",
        "policy_contract_version",
        "policy_version",
        "market_selection_policy_version",
        "fill_model_version",
        "step_schedule_policy_version",
        "maximum_market_age_us",
        "step_window_duration_us",
        "step_gap_us",
        "maximum_steps",
        "partial_fill_mode",
        "partial_fill_fraction",
        "slippage_basis_points",
        "no_fill_terminal_order_state",
        "incomplete_terminal_order_state",
    )
    values = (
        p.paper_fill_policy_id,
        p.policy_contract_version,
        p.policy_version,
        p.market_selection_policy_version,
        p.fill_model_version,
        p.step_schedule_policy_version,
        _timedelta_us(p.maximum_market_age),
        _timedelta_us(p.step_window_duration),
        _timedelta_us(p.step_gap),
        p.maximum_steps,
        p.partial_fill_mode.value,
        None if p.partial_fill_fraction is None else str(p.partial_fill_fraction),
        str(p.slippage_basis_points),
        p.no_fill_terminal_order_state.value,
        p.incomplete_terminal_order_state.value,
    )
    return columns, values


def _hydrate_fill_policy_row(row: sqlite3.Row) -> PaperFillPolicy:
    return PaperFillPolicy(
        row["paper_fill_policy_id"],
        row["policy_contract_version"],
        row["policy_version"],
        row["market_selection_policy_version"],
        row["fill_model_version"],
        row["step_schedule_policy_version"],
        timedelta(microseconds=int(row["maximum_market_age_us"])),
        timedelta(microseconds=int(row["step_window_duration_us"])),
        timedelta(microseconds=int(row["step_gap_us"])),
        int(row["maximum_steps"]),
        PaperPartialFillMode(row["partial_fill_mode"]),
        None if row["partial_fill_fraction"] is None else Decimal(row["partial_fill_fraction"]),
        Decimal(row["slippage_basis_points"]),
        PaperOrderState(row["no_fill_terminal_order_state"]),
        PaperOrderState(row["incomplete_terminal_order_state"]),
    )


def _account_bootstrap_row(b: PaperAccountBootstrap) -> _Row:
    columns = (
        "paper_account_id",
        "bootstrap_contract_version",
        "initial_cash",
        "settlement_currency",
        "margin_policy_version",
        "leverage",
        "unrealized_mark_policy_version",
    )
    values = (
        b.paper_account_id,
        b.bootstrap_contract_version,
        str(b.initial_cash),
        b.settlement_currency.code,
        b.margin_policy_version,
        str(b.leverage),
        b.unrealized_mark_policy_version,
    )
    return columns, values


def _hydrate_account_bootstrap_row(row: sqlite3.Row) -> PaperAccountBootstrap:
    return PaperAccountBootstrap(
        row["paper_account_id"],
        row["bootstrap_contract_version"],
        Decimal(row["initial_cash"]),
        Currency(row["settlement_currency"]),
        row["margin_policy_version"],
        Decimal(row["leverage"]),
        row["unrealized_mark_policy_version"],
    )


def _order_row(o: PaperOrder) -> _Row:
    columns = (
        "paper_order_id",
        "order_contract_version",
        "paper_account_id",
        "intent_kind",
        "source_intent_id",
        "source_intent_idempotency_key",
        "source_intent_content_digest",
        "paper_position_id",
        "pair",
        "side",
        "original_quantity",
        "authority",
        "fill_policy_id",
        "intent_created_at",
        "created_at",
    )
    lineage = o.intent_lineage
    values = (
        o.paper_order_id,
        o.order_contract_version,
        o.paper_account_id,
        lineage.intent_kind.value,
        lineage.source_intent_id,
        lineage.source_intent_idempotency_key,
        lineage.source_intent_content_digest,
        lineage.paper_position_id,
        o.pair.symbol,
        o.side.value,
        str(o.original_quantity),
        o.authority.value,
        o.fill_policy_id,
        _dt(o.intent_created_at),
        _dt(o.created_at),
    )
    return columns, values


def _hydrate_order_row(row: sqlite3.Row) -> PaperOrder:
    lineage = PaperOrderIntentLineage(
        PaperIntentKind(row["intent_kind"]),
        row["source_intent_id"],
        row["source_intent_idempotency_key"],
        row["source_intent_content_digest"],
        row["paper_position_id"],
    )
    return PaperOrder(
        row["paper_order_id"],
        row["order_contract_version"],
        row["paper_account_id"],
        lineage,
        CurrencyPair.parse(row["pair"]),
        Side(row["side"]),
        Decimal(row["original_quantity"]),
        ExecutionAuthorityMode(row["authority"]),
        row["fill_policy_id"],
        datetime.fromisoformat(row["intent_created_at"]),
        datetime.fromisoformat(row["created_at"]),
    )


def _order_event_row(e: PaperOrderEvent) -> _Row:
    columns = (
        "paper_order_event_id",
        "paper_order_id",
        "event_ordinal",
        "state",
        "source_evidence_kind",
        "source_evidence_id",
        "appended_at",
    )
    values = (
        e.paper_order_event_id,
        e.paper_order_id,
        e.event_ordinal,
        e.state.value,
        e.source_evidence_kind,
        e.source_evidence_id,
        _dt(e.appended_at),
    )
    return columns, values


def _hydrate_order_event(row: sqlite3.Row) -> PaperOrderEvent:
    return PaperOrderEvent(
        row["paper_order_event_id"],
        row["paper_order_id"],
        int(row["event_ordinal"]),
        PaperOrderState(row["state"]),
        row["source_evidence_kind"],
        row["source_evidence_id"],
        datetime.fromisoformat(row["appended_at"]),
    )


def _plan_row(p: FillEvaluationPlan) -> _Row:
    columns = (
        "fill_evaluation_plan_id",
        "plan_contract_version",
        "paper_order_id",
        "pair",
        "side",
        "original_quantity",
        "fill_policy_id",
        "intent_created_at",
        "maximum_steps",
        "plan_expiry_at",
        "created_at",
    )
    values = (
        p.fill_evaluation_plan_id,
        p.plan_contract_version,
        p.paper_order_id,
        p.pair.symbol,
        p.side.value,
        str(p.original_quantity),
        p.fill_policy_id,
        _dt(p.intent_created_at),
        p.maximum_steps,
        _dt(p.plan_expiry_at),
        _dt(p.created_at),
    )
    return columns, values


def _hydrate_plan_row(
    row: sqlite3.Row, intent_lineage: PaperOrderIntentLineage
) -> FillEvaluationPlan:
    return FillEvaluationPlan(
        row["fill_evaluation_plan_id"],
        row["plan_contract_version"],
        row["paper_order_id"],
        intent_lineage,
        CurrencyPair.parse(row["pair"]),
        Side(row["side"]),
        Decimal(row["original_quantity"]),
        row["fill_policy_id"],
        datetime.fromisoformat(row["intent_created_at"]),
        int(row["maximum_steps"]),
        datetime.fromisoformat(row["plan_expiry_at"]),
        datetime.fromisoformat(row["created_at"]),
    )


def _step_row(s: FillEvaluationStep) -> _Row:
    columns = (
        "fill_evaluation_step_id",
        "step_contract_version",
        "fill_evaluation_plan_id",
        "ordinal",
        "evaluation_window_start_at",
        "evaluation_due_at",
        "remaining_quantity_before",
        "fill_policy_id",
        "created_at",
    )
    values = (
        s.fill_evaluation_step_id,
        s.step_contract_version,
        s.fill_evaluation_plan_id,
        s.ordinal,
        _dt(s.evaluation_window_start_at),
        _dt(s.evaluation_due_at),
        str(s.remaining_quantity_before),
        s.fill_policy_id,
        _dt(s.created_at),
    )
    return columns, values


def _hydrate_step(row: sqlite3.Row) -> FillEvaluationStep:
    return FillEvaluationStep(
        row["fill_evaluation_step_id"],
        row["step_contract_version"],
        row["fill_evaluation_plan_id"],
        int(row["ordinal"]),
        datetime.fromisoformat(row["evaluation_window_start_at"]),
        datetime.fromisoformat(row["evaluation_due_at"]),
        Decimal(row["remaining_quantity_before"]),
        row["fill_policy_id"],
        datetime.fromisoformat(row["created_at"]),
    )


def _attempt_row(a: FillEvaluationAttempt) -> _Row:
    columns = (
        "fill_evaluation_attempt_id",
        "fill_evaluation_step_id",
        "evaluated_at",
        "disposition",
        "diagnostic_code",
        "worker_identity",
        "created_at",
    )
    values = (
        a.fill_evaluation_attempt_id,
        a.fill_evaluation_step_id,
        _dt(a.evaluated_at),
        a.disposition,
        a.diagnostic_code.value,
        a.worker_identity,
        _dt(a.created_at),
    )
    return columns, values


def _hydrate_attempt(row: sqlite3.Row) -> FillEvaluationAttempt:
    return FillEvaluationAttempt(
        row["fill_evaluation_attempt_id"],
        row["fill_evaluation_step_id"],
        datetime.fromisoformat(row["evaluated_at"]),
        row["disposition"],
        PaperAttemptDiagnosticCode(row["diagnostic_code"]),
        row["worker_identity"],
        datetime.fromisoformat(row["created_at"]),
    )


def _selection_row(s: PaperMarketObservationSelection) -> _Row:
    columns = (
        "market_observation_selection_id",
        "fill_evaluation_step_id",
        "fill_evaluation_plan_id",
        "market_observation_id",
        "market_selection_policy_version",
        "evaluation_window_start_at",
        "evaluation_due_at",
        "intent_created_at",
        "selected_at",
    )
    values = (
        s.market_observation_selection_id,
        s.fill_evaluation_step_id,
        s.fill_evaluation_plan_id,
        s.market_observation_id,
        s.market_selection_policy_version,
        _dt(s.evaluation_window_start_at),
        _dt(s.evaluation_due_at),
        _dt(s.intent_created_at),
        _dt(s.selected_at),
    )
    return columns, values


def _hydrate_selection(row: sqlite3.Row) -> PaperMarketObservationSelection:
    return PaperMarketObservationSelection(
        row["market_observation_selection_id"],
        row["fill_evaluation_step_id"],
        row["fill_evaluation_plan_id"],
        row["market_observation_id"],
        row["market_selection_policy_version"],
        datetime.fromisoformat(row["evaluation_window_start_at"]),
        datetime.fromisoformat(row["evaluation_due_at"]),
        datetime.fromisoformat(row["intent_created_at"]),
        datetime.fromisoformat(row["selected_at"]),
    )


def _no_market_outcome_row(o: PaperNoMarketOutcome) -> _Row:
    columns = (
        "no_market_outcome_id",
        "fill_evaluation_step_id",
        "terminal_reason_code",
        "evaluation_due_at",
        "resolved_at",
    )
    values = (
        o.no_market_outcome_id,
        o.fill_evaluation_step_id,
        o.terminal_reason_code,
        _dt(o.evaluation_due_at),
        _dt(o.resolved_at),
    )
    return columns, values


def _hydrate_no_market_outcome(row: sqlite3.Row) -> PaperNoMarketOutcome:
    return PaperNoMarketOutcome(
        row["no_market_outcome_id"],
        row["fill_evaluation_step_id"],
        row["terminal_reason_code"],
        datetime.fromisoformat(row["evaluation_due_at"]),
        datetime.fromisoformat(row["resolved_at"]),
    )


def _fill_row(f: PaperFill) -> _Row:
    columns = (
        "paper_fill_id",
        "fill_contract_version",
        "fill_evaluation_step_id",
        "market_observation_selection_id",
        "market_observation_id",
        "pair",
        "side",
        "fill_quantity",
        "fill_price",
        "reference_price",
        "slippage_basis_points",
        "fill_model_version",
        "remaining_quantity_before",
        "remaining_quantity_after",
        "created_at",
    )
    values = (
        f.paper_fill_id,
        f.fill_contract_version,
        f.fill_evaluation_step_id,
        f.market_observation_selection_id,
        f.market_observation_id,
        f.pair.symbol,
        f.side.value,
        str(f.fill_quantity),
        str(f.fill_price),
        str(f.reference_price),
        str(f.slippage_basis_points),
        f.fill_model_version,
        str(f.remaining_quantity_before),
        str(f.remaining_quantity_after),
        _dt(f.created_at),
    )
    return columns, values


def _hydrate_fill(row: sqlite3.Row) -> PaperFill:
    return PaperFill(
        row["paper_fill_id"],
        row["fill_contract_version"],
        row["fill_evaluation_step_id"],
        row["market_observation_selection_id"],
        row["market_observation_id"],
        CurrencyPair.parse(row["pair"]),
        Side(row["side"]),
        Decimal(row["fill_quantity"]),
        Decimal(row["fill_price"]),
        Decimal(row["reference_price"]),
        Decimal(row["slippage_basis_points"]),
        row["fill_model_version"],
        Decimal(row["remaining_quantity_before"]),
        Decimal(row["remaining_quantity_after"]),
        datetime.fromisoformat(row["created_at"]),
    )


def _position_row(p: PaperPositionRecord) -> _Row:
    columns = (
        "paper_position_id",
        "position_contract_version",
        "paper_account_id",
        "entry_paper_order_id",
        "pair",
        "position_side",
        "created_at",
    )
    values = (
        p.paper_position_id,
        p.position_contract_version,
        p.paper_account_id,
        p.entry_paper_order_id,
        p.pair.symbol,
        p.position_side.value,
        _dt(p.created_at),
    )
    return columns, values


def _hydrate_position_record(row: sqlite3.Row) -> PaperPositionRecord:
    return PaperPositionRecord(
        row["paper_position_id"],
        row["position_contract_version"],
        row["paper_account_id"],
        row["entry_paper_order_id"],
        CurrencyPair.parse(row["pair"]),
        PaperPositionSide(row["position_side"]),
        datetime.fromisoformat(row["created_at"]),
    )


def _application_row(a: PaperPositionFillApplication) -> _Row:
    columns = (
        "paper_position_fill_application_id",
        "application_contract_version",
        "paper_position_id",
        "paper_order_id",
        "paper_fill_id",
        "application_kind",
        "quantity",
        "price",
        "open_quantity_after",
        "realized_pnl_amount",
        "created_at",
    )
    values = (
        a.paper_position_fill_application_id,
        a.application_contract_version,
        a.paper_position_id,
        a.paper_order_id,
        a.paper_fill_id,
        a.application_kind.value,
        str(a.quantity),
        str(a.price),
        str(a.open_quantity_after),
        None if a.realized_pnl_amount is None else str(a.realized_pnl_amount),
        _dt(a.created_at),
    )
    return columns, values


def _hydrate_application(row: sqlite3.Row) -> PaperPositionFillApplication:
    return PaperPositionFillApplication(
        row["paper_position_fill_application_id"],
        row["application_contract_version"],
        row["paper_position_id"],
        row["paper_order_id"],
        row["paper_fill_id"],
        PaperPositionApplicationKind(row["application_kind"]),
        Decimal(row["quantity"]),
        Decimal(row["price"]),
        Decimal(row["open_quantity_after"]),
        None if row["realized_pnl_amount"] is None else Decimal(row["realized_pnl_amount"]),
        datetime.fromisoformat(row["created_at"]),
    )


def _ledger_entry_row(e: PaperLedgerEntry) -> _Row:
    columns = (
        "ledger_entry_id",
        "entry_contract_version",
        "paper_account_id",
        "paper_position_id",
        "entry_kind",
        "settlement_currency",
        "amount",
        "source_evidence_kind",
        "source_evidence_id",
        "formula_version",
        "created_at",
    )
    values = (
        e.ledger_entry_id,
        e.entry_contract_version,
        e.paper_account_id,
        e.paper_position_id,
        e.entry_kind.value,
        e.settlement_currency.code,
        str(e.amount),
        e.source_evidence_kind,
        e.source_evidence_id,
        e.formula_version,
        _dt(e.created_at),
    )
    return columns, values


def _hydrate_ledger_entry(row: sqlite3.Row) -> PaperLedgerEntry:
    return PaperLedgerEntry(
        row["ledger_entry_id"],
        row["entry_contract_version"],
        row["paper_account_id"],
        row["paper_position_id"],
        PaperLedgerEntryKind(row["entry_kind"]),
        Currency(row["settlement_currency"]),
        Decimal(row["amount"]),
        row["source_evidence_kind"],
        row["source_evidence_id"],
        row["formula_version"],
        datetime.fromisoformat(row["created_at"]),
    )


def _position_snapshot_row(s: PaperPositionSnapshot) -> _Row:
    columns = (
        "paper_position_snapshot_id",
        "snapshot_contract_version",
        "paper_account_id",
        "paper_position_id",
        "pair",
        "position_side",
        "open_quantity",
        "average_entry_price",
        "realized_pnl_total",
        "accrued_swap_total",
        "highest_application_seq",
        "highest_ledger_entry_seq",
        "average_entry_price_formula_version",
        "realized_pnl_formula_version",
        "swap_accrual_formula_version",
        "created_at",
    )
    values = (
        s.paper_position_snapshot_id,
        s.snapshot_contract_version,
        s.paper_account_id,
        s.paper_position_id,
        s.pair.symbol,
        s.position_side.value,
        str(s.open_quantity),
        str(s.average_entry_price),
        str(s.realized_pnl_total),
        str(s.accrued_swap_total),
        s.highest_application_seq,
        s.highest_ledger_entry_seq,
        s.average_entry_price_formula_version,
        s.realized_pnl_formula_version,
        s.swap_accrual_formula_version,
        _dt(s.created_at),
    )
    return columns, values


def _hydrate_position_snapshot(row: sqlite3.Row) -> PaperPositionSnapshot:
    return PaperPositionSnapshot(
        row["paper_position_snapshot_id"],
        row["snapshot_contract_version"],
        row["paper_account_id"],
        row["paper_position_id"],
        CurrencyPair.parse(row["pair"]),
        PaperPositionSide(row["position_side"]),
        Decimal(row["open_quantity"]),
        Decimal(row["average_entry_price"]),
        Decimal(row["realized_pnl_total"]),
        Decimal(row["accrued_swap_total"]),
        int(row["highest_application_seq"]),
        int(row["highest_ledger_entry_seq"]),
        row["average_entry_price_formula_version"],
        row["realized_pnl_formula_version"],
        row["swap_accrual_formula_version"],
        datetime.fromisoformat(row["created_at"]),
    )


def _account_snapshot_row(s: PaperAccountSnapshot) -> _Row:
    columns = (
        "paper_account_snapshot_id",
        "snapshot_contract_version",
        "paper_account_id",
        "cash",
        "realized_pnl_total",
        "unrealized_pnl_total",
        "accrued_swap_total",
        "equity",
        "used_margin",
        "available_margin",
        "gross_exposure",
        "open_position_count",
        "open_order_count",
        "mark_observation_ids_json",
        "highest_application_seq",
        "highest_ledger_entry_seq",
        "highest_order_event_seq",
        "margin_policy_version",
        "unrealized_mark_policy_version",
        "formula_versions_json",
        "created_at",
    )
    values = (
        s.paper_account_snapshot_id,
        s.snapshot_contract_version,
        s.paper_account_id,
        str(s.cash),
        str(s.realized_pnl_total),
        str(s.unrealized_pnl_total),
        str(s.accrued_swap_total),
        str(s.equity),
        str(s.used_margin),
        str(s.available_margin),
        str(s.gross_exposure),
        s.open_position_count,
        s.open_order_count,
        json.dumps(list(s.mark_observation_ids)),
        s.highest_application_seq,
        s.highest_ledger_entry_seq,
        s.highest_order_event_seq,
        s.margin_policy_version,
        s.unrealized_mark_policy_version,
        json.dumps(list(s.formula_versions)),
        _dt(s.created_at),
    )
    return columns, values


def _hydrate_account_snapshot(row: sqlite3.Row) -> PaperAccountSnapshot:
    return PaperAccountSnapshot(
        row["paper_account_snapshot_id"],
        row["snapshot_contract_version"],
        row["paper_account_id"],
        Decimal(row["cash"]),
        Decimal(row["realized_pnl_total"]),
        Decimal(row["unrealized_pnl_total"]),
        Decimal(row["accrued_swap_total"]),
        Decimal(row["equity"]),
        Decimal(row["used_margin"]),
        Decimal(row["available_margin"]),
        Decimal(row["gross_exposure"]),
        int(row["open_position_count"]),
        int(row["open_order_count"]),
        tuple(json.loads(row["mark_observation_ids_json"])),
        int(row["highest_application_seq"]),
        int(row["highest_ledger_entry_seq"]),
        int(row["highest_order_event_seq"]),
        row["margin_policy_version"],
        row["unrealized_mark_policy_version"],
        tuple(json.loads(row["formula_versions_json"])),
        datetime.fromisoformat(row["created_at"]),
    )


def _swap_accrual_row(a: PaperSwapAccrual) -> _Row:
    columns = (
        "paper_swap_accrual_id",
        "accrual_contract_version",
        "paper_position_id",
        "paper_position_snapshot_id",
        "swap_evidence_id",
        "rollover_date",
        "open_quantity",
        "unit_basis",
        "base_units_per_unit",
        "settlement_currency",
        "policy_version",
        "formula_version",
        "amount",
        "created_at",
    )
    values = (
        a.paper_swap_accrual_id,
        a.accrual_contract_version,
        a.paper_position_id,
        a.paper_position_snapshot_id,
        a.swap_evidence_id,
        a.rollover_date.isoformat(),
        str(a.open_quantity),
        a.unit_basis,
        str(a.base_units_per_unit),
        a.settlement_currency.code,
        a.policy_version,
        a.formula_version,
        str(a.amount),
        _dt(a.created_at),
    )
    return columns, values


def _hydrate_swap_accrual(row: sqlite3.Row) -> PaperSwapAccrual:
    return PaperSwapAccrual(
        row["paper_swap_accrual_id"],
        row["accrual_contract_version"],
        row["paper_position_id"],
        row["paper_position_snapshot_id"],
        row["swap_evidence_id"],
        date.fromisoformat(row["rollover_date"]),
        Decimal(row["open_quantity"]),
        row["unit_basis"],
        Decimal(row["base_units_per_unit"]),
        Currency(row["settlement_currency"]),
        row["policy_version"],
        row["formula_version"],
        Decimal(row["amount"]),
        datetime.fromisoformat(row["created_at"]),
    )


def _swap_non_accrual_row(n: PaperSwapNonAccrual) -> _Row:
    columns = (
        "paper_swap_non_accrual_id",
        "non_accrual_contract_version",
        "paper_position_id",
        "paper_position_snapshot_id",
        "swap_evidence_id",
        "rollover_date",
        "outcome",
        "policy_version",
        "created_at",
    )
    values = (
        n.paper_swap_non_accrual_id,
        n.non_accrual_contract_version,
        n.paper_position_id,
        n.paper_position_snapshot_id,
        n.swap_evidence_id,
        n.rollover_date.isoformat(),
        n.outcome.value,
        n.policy_version,
        _dt(n.created_at),
    )
    return columns, values


def _hydrate_swap_non_accrual(row: sqlite3.Row) -> PaperSwapNonAccrual:
    return PaperSwapNonAccrual(
        row["paper_swap_non_accrual_id"],
        row["non_accrual_contract_version"],
        row["paper_position_id"],
        row["paper_position_snapshot_id"],
        row["swap_evidence_id"],
        date.fromisoformat(row["rollover_date"]),
        PaperSwapAccrualOutcome(row["outcome"]),
        row["policy_version"],
        datetime.fromisoformat(row["created_at"]),
    )


def _swap_correction_row(c: PaperSwapAccrualCorrection) -> _Row:
    columns = (
        "correction_id",
        "correction_contract_version",
        "corrected_accrual_id",
        "chain_ordinal",
        "predecessor_correction_id",
        "effective_amount_before",
        "replacement_amount",
        "delta_amount",
        "correction_reason",
        "swap_evidence_id",
        "created_at",
    )
    values = (
        c.correction_id,
        c.correction_contract_version,
        c.corrected_accrual_id,
        c.chain_ordinal,
        c.predecessor_correction_id,
        str(c.effective_amount_before),
        str(c.replacement_amount),
        str(c.delta_amount),
        c.correction_reason,
        c.swap_evidence_id,
        _dt(c.created_at),
    )
    return columns, values


def _hydrate_swap_correction(row: sqlite3.Row) -> PaperSwapAccrualCorrection:
    return PaperSwapAccrualCorrection(
        row["correction_id"],
        row["correction_contract_version"],
        row["corrected_accrual_id"],
        int(row["chain_ordinal"]),
        row["predecessor_correction_id"],
        Decimal(row["effective_amount_before"]),
        Decimal(row["replacement_amount"]),
        Decimal(row["delta_amount"]),
        row["correction_reason"],
        row["swap_evidence_id"],
        datetime.fromisoformat(row["created_at"]),
    )


def _consumption_row(c: ReservationConsumptionEvidence) -> _Row:
    columns = (
        "consumption_id",
        "contract_version",
        "close_intent_idempotency_key",
        "paper_order_id",
        "paper_fill_id",
        "consumed_quantity",
        "created_at",
    )
    values = (
        c.consumption_id,
        c.contract_version,
        c.close_intent_idempotency_key,
        c.paper_order_id,
        c.paper_fill_id,
        str(c.consumed_quantity),
        _dt(c.created_at),
    )
    return columns, values


def _hydrate_consumption(row: sqlite3.Row) -> ReservationConsumptionEvidence:
    return ReservationConsumptionEvidence(
        row["consumption_id"],
        row["contract_version"],
        row["close_intent_idempotency_key"],
        row["paper_order_id"],
        row["paper_fill_id"],
        Decimal(row["consumed_quantity"]),
        datetime.fromisoformat(row["created_at"]),
    )


def _release_row(r: ReservationReleaseEvidence) -> _Row:
    columns = (
        "release_id",
        "contract_version",
        "close_intent_idempotency_key",
        "paper_order_id",
        "terminal_order_state",
        "released_quantity",
        "created_at",
    )
    values = (
        r.release_id,
        r.contract_version,
        r.close_intent_idempotency_key,
        r.paper_order_id,
        r.terminal_order_state.value,
        str(r.released_quantity),
        _dt(r.created_at),
    )
    return columns, values


def _hydrate_release(row: sqlite3.Row) -> ReservationReleaseEvidence:
    return ReservationReleaseEvidence(
        row["release_id"],
        row["contract_version"],
        row["close_intent_idempotency_key"],
        row["paper_order_id"],
        PaperOrderState(row["terminal_order_state"]),
        Decimal(row["released_quantity"]),
        datetime.fromisoformat(row["created_at"]),
    )


def _reconciliation_row(r: PaperReconciliationResult) -> _Row:
    columns = (
        "reconciliation_result_id",
        "result_contract_version",
        "paper_account_id",
        "outcome",
        "reconciled_position_ids_json",
        "highest_application_seq",
        "highest_ledger_entry_seq",
        "highest_order_event_seq",
        "mismatched_record_kinds_json",
        "mismatched_record_ids_json",
        "created_at",
    )
    values = (
        r.reconciliation_result_id,
        r.result_contract_version,
        r.paper_account_id,
        r.outcome.value,
        json.dumps(list(r.reconciled_position_ids)),
        r.highest_application_seq,
        r.highest_ledger_entry_seq,
        r.highest_order_event_seq,
        json.dumps([kind.value for kind in r.mismatched_record_kinds]),
        json.dumps(list(r.mismatched_record_ids)),
        _dt(r.created_at),
    )
    return columns, values


def _application_seq_of(connection: sqlite3.Connection, application_id: str) -> int:
    row = connection.execute(
        "SELECT application_seq FROM live_paper_position_fill_applications "
        "WHERE paper_position_fill_application_id = ?",
        (application_id,),
    ).fetchone()
    return int(row["application_seq"])


def _ledger_entry_seq_of(connection: sqlite3.Connection, ledger_entry_id: str) -> int:
    row = connection.execute(
        "SELECT ledger_entry_seq FROM live_paper_ledger_entries WHERE ledger_entry_id = ?",
        (ledger_entry_id,),
    ).fetchone()
    return int(row["ledger_entry_seq"])


def _order_event_seq_of(connection: sqlite3.Connection, event_id: str) -> int:
    row = connection.execute(
        "SELECT order_event_seq FROM live_paper_order_events WHERE paper_order_event_id = ?",
        (event_id,),
    ).fetchone()
    return int(row["order_event_seq"])


# ---------------------------------------------------------------------------
# SQLitePaperStore
# ---------------------------------------------------------------------------


class SQLitePaperStore:
    """The only Paper module permitted to import sqlite3 or live_migrations."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            migrate_live_database(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    # -- T0 -----------------------------------------------------------------

    def append_market_observations(
        self, observations: Sequence[PaperMarketObservation]
    ) -> tuple[PaperMarketObservation, ...]:
        ordered = tuple(observations)
        for observation in ordered:
            if type(observation) is not PaperMarketObservation:
                raise TypeError("observations entries must be exact PaperMarketObservation")
            PaperMarketObservation.__post_init__(observation)
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                for observation in ordered:
                    _insert_or_compare(
                        connection,
                        "live_paper_market_observations",
                        *_market_observation_row(observation),
                        ("market_observation_id",),
                        "market observation already persisted with different content",
                    )
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return ordered

    # -- T1 -------------------------------------------------------------

    def accept_entry_order(
        self,
        *,
        fill_policy: PaperFillPolicy,
        account_bootstrap: PaperAccountBootstrap,
        intent: ApprovedExecutionIntent,
        evaluated_at: datetime,
    ) -> AcceptedOrder:
        if type(intent) is not ApprovedExecutionIntent:
            raise TypeError("intent must be exact ApprovedExecutionIntent")
        ApprovedExecutionIntent.__post_init__(intent)
        lineage = PaperOrderIntentLineage.for_entry(intent)
        return self._accept_order(
            fill_policy=fill_policy,
            account_bootstrap=account_bootstrap,
            lineage=lineage,
            pair=intent.pair,
            side=intent.side,
            quantity=intent.quantity,
            intent_created_at=intent.created_at,
            evaluated_at=evaluated_at,
            reduce_only=None,
            ordinary_close_intent=None,
        )

    def accept_ordinary_close_order(
        self,
        *,
        fill_policy: PaperFillPolicy,
        account_bootstrap: PaperAccountBootstrap,
        intent: ApprovedCloseIntent,
        evaluated_at: datetime,
    ) -> AcceptedOrder:
        if type(intent) is not ApprovedCloseIntent:
            raise TypeError("intent must be exact ApprovedCloseIntent")
        ApprovedCloseIntent.__post_init__(intent)
        if intent.authority is not ExecutionAuthorityMode.PAPER:
            raise PaperPersistenceIntegrityError(
                "ApprovedCloseIntent authority must be PAPER for the ordinary-close entry point"
            )
        lineage = PaperOrderIntentLineage.for_ordinary_close(intent)
        return self._accept_order(
            fill_policy=fill_policy,
            account_bootstrap=account_bootstrap,
            lineage=lineage,
            pair=intent.pair,
            side=intent.side,
            quantity=intent.quantity,
            intent_created_at=intent.created_at,
            evaluated_at=evaluated_at,
            reduce_only=_ReduceOnlyAttach(
                intent.position_id.value, _position_side_for(opposite_side(intent.side))
            ),
            ordinary_close_intent=intent,
        )

    def accept_emergency_liquidation_order(
        self,
        *,
        fill_policy: PaperFillPolicy,
        account_bootstrap: PaperAccountBootstrap,
        intent: ApprovedLiquidationIntent,
        existing_position_side: Side,
        evaluated_at: datetime,
    ) -> AcceptedOrder:
        if type(intent) is not ApprovedLiquidationIntent:
            raise TypeError("intent must be exact ApprovedLiquidationIntent")
        ApprovedLiquidationIntent.__post_init__(intent)
        lineage = PaperOrderIntentLineage.for_emergency_liquidation(
            intent, existing_position_side=existing_position_side
        )
        order_side = opposite_side(existing_position_side)
        return self._accept_order(
            fill_policy=fill_policy,
            account_bootstrap=account_bootstrap,
            lineage=lineage,
            pair=intent.pair,
            side=order_side,
            quantity=intent.quantity,
            intent_created_at=intent.created_at,
            evaluated_at=evaluated_at,
            reduce_only=_ReduceOnlyAttach(
                intent.position_id.value, _position_side_for(existing_position_side)
            ),
            ordinary_close_intent=None,
        )

    def _accept_order(
        self,
        *,
        fill_policy: PaperFillPolicy,
        account_bootstrap: PaperAccountBootstrap,
        lineage: PaperOrderIntentLineage,
        pair: CurrencyPair,
        side: Side,
        quantity: Decimal,
        intent_created_at: datetime,
        evaluated_at: datetime,
        reduce_only: _ReduceOnlyAttach | None,
        ordinary_close_intent: ApprovedCloseIntent | None,
    ) -> AcceptedOrder:
        if type(fill_policy) is not PaperFillPolicy:
            raise TypeError("fill_policy must be exact PaperFillPolicy")
        PaperFillPolicy.__post_init__(fill_policy)
        if type(account_bootstrap) is not PaperAccountBootstrap:
            raise TypeError("account_bootstrap must be exact PaperAccountBootstrap")
        PaperAccountBootstrap.__post_init__(account_bootstrap)
        if type(pair) is not CurrencyPair:
            raise TypeError("pair must be exact CurrencyPair")
        if pair.quote != _JPY:
            raise PaperPersistenceIntegrityError("Paper order entry requires a JPY-quoted Pair")
        _require_utc_datetime(evaluated_at, "evaluated_at")
        _require_utc_datetime(intent_created_at, "intent_created_at")
        if evaluated_at < intent_created_at:
            raise PaperPersistenceIntegrityError("evaluated_at must not precede intent_created_at")

        plan_expiry_at = (
            intent_created_at
            + fill_policy.step_window_duration * fill_policy.maximum_steps
            + fill_policy.step_gap * (fill_policy.maximum_steps - 1)
        )

        order = PaperOrder.create(
            paper_account_id=account_bootstrap.paper_account_id,
            intent_lineage=lineage,
            pair=pair,
            side=side,
            original_quantity=quantity,
            authority=ExecutionAuthorityMode.PAPER,
            fill_policy_id=fill_policy.paper_fill_policy_id,
            intent_created_at=intent_created_at,
            created_at=evaluated_at,
        )
        plan = FillEvaluationPlan.create(
            paper_order_id=order.paper_order_id,
            intent_lineage=lineage,
            pair=pair,
            side=side,
            original_quantity=quantity,
            fill_policy_id=fill_policy.paper_fill_policy_id,
            intent_created_at=intent_created_at,
            maximum_steps=fill_policy.maximum_steps,
            plan_expiry_at=plan_expiry_at,
            created_at=evaluated_at,
        )
        accepted_event = PaperOrderEvent.create(
            paper_order_id=order.paper_order_id,
            event_ordinal=0,
            state=PaperOrderState.ACCEPTED,
            source_evidence_kind="PAPER_ORDER_ACCEPTANCE",
            source_evidence_id=None,
            appended_at=evaluated_at,
        )

        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                _insert_or_compare(
                    connection,
                    "live_paper_fill_policies",
                    *_fill_policy_row(fill_policy),
                    ("paper_fill_policy_id",),
                    "fill policy already persisted with different content",
                )
                _insert_or_compare(
                    connection,
                    "live_paper_account_bootstraps",
                    *_account_bootstrap_row(account_bootstrap),
                    ("paper_account_id",),
                    "account bootstrap already persisted with different content",
                )

                floor = self._max_plan_instant(
                    connection, plan.fill_evaluation_plan_id, order.paper_order_id
                )
                if floor is not None and evaluated_at < floor:
                    raise PaperPersistenceIntegrityError("evaluated_at regressed for this plan")

                if reduce_only is not None:
                    position = self._hydrate_position(connection, reduce_only.paper_position_id)
                    if position is None:
                        raise PaperPersistenceIntegrityError(
                            "reduce-only order requires an existing Paper position"
                        )
                    if position.pair != pair:
                        raise PaperPersistenceIntegrityError(
                            "reduce-only order Pair must match the position Pair"
                        )
                    if position.paper_account_id != account_bootstrap.paper_account_id:
                        raise PaperPersistenceIntegrityError(
                            "reduce-only order account must match the position account"
                        )
                    if position.position_side is not reduce_only.expected_position_side:
                        raise PaperPersistenceIntegrityError(
                            "reduce-only order Side does not match the position Side"
                        )

                if ordinary_close_intent is not None:
                    self._authenticate_ordinary_close_intent(connection, ordinary_close_intent)

                _insert_or_compare(
                    connection,
                    "live_paper_orders",
                    *_order_row(order),
                    ("paper_order_id",),
                    "Paper order already persisted with different content",
                )
                _insert_or_compare(
                    connection,
                    "live_paper_order_events",
                    *_order_event_row(accepted_event),
                    ("paper_order_event_id",),
                    "Paper order event already persisted with different content",
                )
                _insert_or_compare(
                    connection,
                    "live_paper_fill_evaluation_plans",
                    *_plan_row(plan),
                    ("fill_evaluation_plan_id",),
                    "Paper fill evaluation plan already persisted with different content",
                )
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return AcceptedOrder(order, accepted_event, plan)

    def hydrate_accepted_order(self, *, paper_order_id: str) -> AcceptedOrder | None:
        """The read-only lookup B5 uses to skip T1 for an intent already accepted.

        Read-only: no BEGIN IMMEDIATE, no writes. Returns None when no such order is
        persisted, so B5 can fall back to T1 for a genuinely new intent.
        """
        if type(paper_order_id) is not str or not paper_order_id.strip():
            raise ValueError("paper_order_id must be a non-blank exact str")
        with closing(self._connect()) as connection:
            order_row = connection.execute(
                "SELECT * FROM live_paper_orders WHERE paper_order_id = ?", (paper_order_id,)
            ).fetchone()
            if order_row is None:
                return None
            order = _hydrate_order_row(order_row)
            event_row = connection.execute(
                "SELECT * FROM live_paper_order_events "
                "WHERE paper_order_id = ? AND event_ordinal = 0",
                (paper_order_id,),
            ).fetchone()
            if event_row is None:
                raise PaperPersistenceIntegrityError(
                    "Paper order is missing its ordinal-0 ACCEPTED event"
                )
            accepted_event = _hydrate_order_event(event_row)
            plan_row = connection.execute(
                "SELECT * FROM live_paper_fill_evaluation_plans WHERE paper_order_id = ?",
                (paper_order_id,),
            ).fetchone()
            if plan_row is None:
                raise PaperPersistenceIntegrityError(
                    "Paper order is missing its Fill evaluation plan"
                )
            plan = _hydrate_plan_row(plan_row, order.intent_lineage)
            return AcceptedOrder(order, accepted_event, plan)

    # -- T2 -------------------------------------------------------------

    def current_step_ordinal(self, *, plan: FillEvaluationPlan) -> int:
        """The ordinal B5 should target for its next create_step/evaluate_step call.

        Read-only: no BEGIN IMMEDIATE, no writes. Reuses create_step's own
        ordinal-n>0 legitimacy rule (is_next_step_legitimate) so this never diverges
        from what create_step itself would accept.
        """
        if type(plan) is not FillEvaluationPlan:
            raise TypeError("plan must be exact FillEvaluationPlan")
        FillEvaluationPlan.__post_init__(plan)
        with closing(self._connect()) as connection:
            self._authenticate_plan(connection, plan)
            row = connection.execute(
                "SELECT * FROM live_paper_fill_evaluation_steps "
                "WHERE fill_evaluation_plan_id = ? ORDER BY ordinal DESC LIMIT 1",
                (plan.fill_evaluation_plan_id,),
            ).fetchone()
            if row is None:
                return 0
            step = _hydrate_step(row)
            claim_row = connection.execute(
                "SELECT * FROM live_paper_step_terminal_claims "
                "WHERE fill_evaluation_step_id = ?",
                (step.fill_evaluation_step_id,),
            ).fetchone()
            if claim_row is None:
                return step.ordinal
            variant = PaperStepResolutionVariant(claim_row["variant"])
            fill = self._hydrate_fill_for_step(connection, step.fill_evaluation_step_id)
            legitimate = (
                variant is PaperStepResolutionVariant.MARKET_SELECTED
                and fill is not None
                and is_next_step_legitimate(
                    resolution_variant=variant,
                    fill=fill,
                    remaining_quantity_after=fill.remaining_quantity_after,
                    resolved_ordinal=step.ordinal,
                    maximum_steps=plan.maximum_steps,
                )
            )
            return step.ordinal + 1 if legitimate else step.ordinal

    def create_step(
        self, *, plan: FillEvaluationPlan, ordinal: int, evaluated_at: datetime
    ) -> CreatedStep:
        if type(plan) is not FillEvaluationPlan:
            raise TypeError("plan must be exact FillEvaluationPlan")
        FillEvaluationPlan.__post_init__(plan)
        if type(ordinal) is not int or isinstance(ordinal, bool) or ordinal < 0:
            raise ValueError("ordinal must be an exact int >= 0")
        _require_utc_datetime(evaluated_at, "evaluated_at")

        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                order = self._authenticate_plan(connection, plan)

                floor = self._max_plan_instant(
                    connection, plan.fill_evaluation_plan_id, order.paper_order_id
                )
                effective_floor = (
                    plan.intent_created_at if floor is None else max(floor, plan.intent_created_at)
                )
                if evaluated_at < effective_floor:
                    raise PaperPersistenceIntegrityError("evaluated_at regressed for this plan")

                policy = self._hydrate_fill_policy(connection, plan.fill_policy_id)
                if policy is None:
                    raise PaperPersistenceIntegrityError(
                        "plan references a fill policy that is not persisted"
                    )

                if ordinal == 0:
                    window_start = plan.intent_created_at
                    remaining_before = plan.original_quantity
                else:
                    previous_row = connection.execute(
                        "SELECT * FROM live_paper_fill_evaluation_steps "
                        "WHERE fill_evaluation_plan_id = ? AND ordinal = ?",
                        (plan.fill_evaluation_plan_id, ordinal - 1),
                    ).fetchone()
                    if previous_row is None:
                        raise PaperPersistenceIntegrityError(
                            "a later Step requires its immediately preceding Step"
                        )
                    previous_step = _hydrate_step(previous_row)
                    previous_fill = self._hydrate_fill_for_step(
                        connection, previous_step.fill_evaluation_step_id
                    )
                    claim_row = connection.execute(
                        "SELECT * FROM live_paper_step_terminal_claims "
                        "WHERE fill_evaluation_step_id = ?",
                        (previous_step.fill_evaluation_step_id,),
                    ).fetchone()
                    variant = (
                        None
                        if claim_row is None
                        else PaperStepResolutionVariant(claim_row["variant"])
                    )
                    legitimate = (
                        variant is PaperStepResolutionVariant.MARKET_SELECTED
                        and previous_fill is not None
                        and is_next_step_legitimate(
                            resolution_variant=variant,
                            fill=previous_fill,
                            remaining_quantity_after=previous_fill.remaining_quantity_after,
                            resolved_ordinal=previous_step.ordinal,
                            maximum_steps=plan.maximum_steps,
                        )
                    )
                    if not legitimate:
                        raise PaperPersistenceIntegrityError(
                            "a new Step is only legitimate after a MARKET_SELECTED Fill leaving a "
                            "positive remainder with ordinal + 1 < maximum_steps"
                        )
                    assert previous_fill is not None
                    window_start = previous_step.evaluation_due_at + policy.step_gap
                    remaining_before = previous_fill.remaining_quantity_after

                evaluation_due_at = window_start + policy.step_window_duration
                step = FillEvaluationStep.create(
                    fill_evaluation_plan_id=plan.fill_evaluation_plan_id,
                    ordinal=ordinal,
                    evaluation_window_start_at=window_start,
                    evaluation_due_at=evaluation_due_at,
                    remaining_quantity_before=remaining_before,
                    fill_policy_id=plan.fill_policy_id,
                    created_at=evaluated_at,
                )
                _insert_or_compare(
                    connection,
                    "live_paper_fill_evaluation_steps",
                    *_step_row(step),
                    ("fill_evaluation_step_id",),
                    "Paper fill evaluation Step already persisted with different content",
                )

                open_event: PaperOrderEvent | None = None
                if ordinal == 0:
                    existing_open_row = connection.execute(
                        "SELECT * FROM live_paper_order_events "
                        "WHERE paper_order_id = ? AND state = 'OPEN'",
                        (order.paper_order_id,),
                    ).fetchone()
                    if existing_open_row is not None:
                        open_event = _hydrate_order_event(existing_open_row)
                    else:
                        next_ordinal = _scalar_int(
                            connection,
                            "SELECT COALESCE(MAX(event_ordinal), -1) + 1 "
                            "FROM live_paper_order_events WHERE paper_order_id = ?",
                            (order.paper_order_id,),
                        )
                        open_event = PaperOrderEvent.create(
                            paper_order_id=order.paper_order_id,
                            event_ordinal=next_ordinal,
                            state=PaperOrderState.OPEN,
                            source_evidence_kind="PAPER_FILL_EVALUATION_STEP",
                            source_evidence_id=step.fill_evaluation_step_id,
                            appended_at=evaluated_at,
                        )
                        _insert_or_compare(
                            connection,
                            "live_paper_order_events",
                            *_order_event_row(open_event),
                            ("paper_order_event_id",),
                            "Paper order event already persisted with different content",
                        )
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return CreatedStep(step, open_event)

    def hydrate_created_step(
        self, *, plan: FillEvaluationPlan, ordinal: int
    ) -> CreatedStep | None:
        """The read-only counterpart to create_step B5 uses to resume a Step left
        PAPER_STEP_PENDING with a later evaluated_at, instead of re-submitting
        create_step's own audit-only created_at as new content for an ordinal that
        is already persisted (create_step's own _insert_or_compare would otherwise
        reject it as a content mismatch). Read-only: no BEGIN IMMEDIATE, no writes.
        """
        if type(plan) is not FillEvaluationPlan:
            raise TypeError("plan must be exact FillEvaluationPlan")
        FillEvaluationPlan.__post_init__(plan)
        if type(ordinal) is not int or isinstance(ordinal, bool) or ordinal < 0:
            raise ValueError("ordinal must be an exact int >= 0")
        with closing(self._connect()) as connection:
            order = self._authenticate_plan(connection, plan)
            row = connection.execute(
                "SELECT * FROM live_paper_fill_evaluation_steps "
                "WHERE fill_evaluation_plan_id = ? AND ordinal = ?",
                (plan.fill_evaluation_plan_id, ordinal),
            ).fetchone()
            if row is None:
                return None
            step = _hydrate_step(row)
            open_event: PaperOrderEvent | None = None
            if ordinal == 0:
                open_row = connection.execute(
                    "SELECT * FROM live_paper_order_events "
                    "WHERE paper_order_id = ? AND state = 'OPEN'",
                    (order.paper_order_id,),
                ).fetchone()
                open_event = None if open_row is None else _hydrate_order_event(open_row)
            return CreatedStep(step, open_event)

    # -- T3 / T4 ----------------------------------------------------------

    def evaluate_step(
        self,
        *,
        step: FillEvaluationStep,
        plan: FillEvaluationPlan,
        worker_identity: str,
        evaluated_at: datetime,
        mark_observations: tuple[PaperMarketObservation, ...] = (),
    ) -> EvaluatedStep:
        if type(step) is not FillEvaluationStep:
            raise TypeError("step must be exact FillEvaluationStep")
        FillEvaluationStep.__post_init__(step)
        if type(plan) is not FillEvaluationPlan:
            raise TypeError("plan must be exact FillEvaluationPlan")
        FillEvaluationPlan.__post_init__(plan)
        if type(worker_identity) is not str or not worker_identity.strip():
            raise ValueError("worker_identity must be a non-blank exact str")
        _require_utc_datetime(evaluated_at, "evaluated_at")
        if type(mark_observations) is not tuple:
            raise TypeError("mark_observations must be exact tuple")

        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                order = self._authenticate_plan(connection, plan)
                self._authenticate_step(connection, step, plan)
                policy = self._hydrate_fill_policy(connection, plan.fill_policy_id)
                if policy is None:
                    raise PaperPersistenceIntegrityError(
                        "plan references a fill policy that is not persisted"
                    )

                existing_claim = connection.execute(
                    "SELECT * FROM live_paper_step_terminal_claims "
                    "WHERE fill_evaluation_step_id = ?",
                    (step.fill_evaluation_step_id,),
                ).fetchone()
                if existing_claim is not None:
                    result = self._replay_resolved_step(connection, order, existing_claim)
                    connection.commit()
                    return result

                floor = self._max_plan_instant(
                    connection, plan.fill_evaluation_plan_id, order.paper_order_id
                )
                effective_floor = (
                    plan.intent_created_at if floor is None else max(floor, plan.intent_created_at)
                )
                if evaluated_at < effective_floor:
                    raise PaperPersistenceIntegrityError("evaluated_at regressed for this plan")

                already_selected = frozenset(
                    row["market_observation_id"]
                    for row in connection.execute(
                        "SELECT market_observation_id FROM "
                        "live_paper_market_observation_selections "
                        "WHERE fill_evaluation_plan_id = ?",
                        (plan.fill_evaluation_plan_id,),
                    )
                )
                fills_so_far = self._hydrate_ordered_fills_for_plan(
                    connection, plan.fill_evaluation_plan_id
                )
                next_order_event_ordinal = _scalar_int(
                    connection,
                    "SELECT COALESCE(MAX(event_ordinal), -1) + 1 FROM live_paper_order_events "
                    "WHERE paper_order_id = ?",
                    (order.paper_order_id,),
                )
                candidate_observations = self._candidate_observations(
                    connection,
                    plan=plan,
                    step=step,
                    policy=policy,
                    evaluated_at=evaluated_at,
                    already_selected=already_selected,
                )

                evaluation = evaluate_fill_evaluation_step(
                    step=step,
                    plan=plan,
                    policy=policy,
                    candidate_observations=candidate_observations,
                    already_selected_observation_ids=already_selected,
                    fills_so_far=fills_so_far,
                    evaluated_at=evaluated_at,
                    next_order_event_ordinal=next_order_event_ordinal,
                )

                if type(evaluation) is PendingStepEvaluation:
                    result = self._apply_pending(
                        connection,
                        step=step,
                        worker_identity=worker_identity,
                        evaluated_at=evaluated_at,
                        diagnostic_code=evaluation.diagnostic_code,
                    )
                elif type(evaluation) is MarketSelectedStepEvaluation:
                    result = self._apply_market_selected(
                        connection,
                        order=order,
                        evaluation=evaluation,
                        evaluated_at=evaluated_at,
                        mark_observations=mark_observations,
                    )
                else:
                    assert type(evaluation) is NoMarketStepEvaluation
                    result = self._apply_no_market(
                        connection,
                        order=order,
                        evaluation=evaluation,
                        evaluated_at=evaluated_at,
                    )
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return result

    def _candidate_observations(
        self,
        connection: sqlite3.Connection,
        *,
        plan: FillEvaluationPlan,
        step: FillEvaluationStep,
        policy: PaperFillPolicy,
        evaluated_at: datetime,
        already_selected: frozenset[str],
    ) -> tuple[PaperMarketObservation, ...]:
        due_minus_age = step.evaluation_due_at - policy.maximum_market_age
        row = connection.execute(
            "SELECT * FROM live_paper_market_observations "
            "WHERE pair = ? "
            "AND received_at >= ? "
            "AND received_at >= ? "
            "AND received_at <= ? "
            "AND received_at <= ? "
            "AND provider_observed_at <= received_at "
            "AND provider_observed_at <= ? "
            "AND provider_observed_at >= ? "
            "AND market_observation_id NOT IN ("
            "  SELECT market_observation_id FROM live_paper_market_observation_selections "
            "  WHERE fill_evaluation_plan_id = ?) "
            "ORDER BY received_at ASC, provider_observed_at ASC, market_observation_id ASC "
            "LIMIT 1",
            (
                plan.pair.symbol,
                _dt(plan.intent_created_at),
                _dt(step.evaluation_window_start_at),
                _dt(step.evaluation_due_at),
                _dt(evaluated_at),
                _dt(step.evaluation_due_at),
                _dt(due_minus_age),
                plan.fill_evaluation_plan_id,
            ),
        ).fetchone()
        if row is not None:
            observation = _hydrate_market_observation(row)
            eligibility = assess_observation_eligibility(
                observation, step, plan, policy, evaluated_at, already_selected
            )
            if not eligibility.eligible:
                raise PaperPersistenceIntegrityError(
                    "persisted market observation selection query returned an ineligible row"
                )
            return (observation,)
        witness_row = connection.execute(
            "SELECT * FROM live_paper_market_observations WHERE pair = ? "
            "ORDER BY market_observation_id ASC LIMIT 1",
            (plan.pair.symbol,),
        ).fetchone()
        if witness_row is None:
            return ()
        return (_hydrate_market_observation(witness_row),)

    def _apply_pending(
        self,
        connection: sqlite3.Connection,
        *,
        step: FillEvaluationStep,
        worker_identity: str,
        evaluated_at: datetime,
        diagnostic_code: PaperAttemptDiagnosticCode,
    ) -> EvaluatedStep:
        attempt = FillEvaluationAttempt.create(
            fill_evaluation_step_id=step.fill_evaluation_step_id,
            evaluated_at=evaluated_at,
            disposition=PAPER_ATTEMPT_DISPOSITION_PENDING_NO_ELIGIBLE_MARKET,
            diagnostic_code=diagnostic_code,
            worker_identity=worker_identity,
            created_at=evaluated_at,
        )
        _insert_or_compare(
            connection,
            "live_paper_fill_evaluation_attempts",
            *_attempt_row(attempt),
            ("fill_evaluation_attempt_id",),
            "attempt already persisted with different content",
        )
        return EvaluatedStep(
            StepResolutionOutcome.PENDING,
            (),
            attempt,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )

    def _apply_market_selected(
        self,
        connection: sqlite3.Connection,
        *,
        order: PaperOrder,
        evaluation: MarketSelectedStepEvaluation,
        evaluated_at: datetime,
        mark_observations: tuple[PaperMarketObservation, ...],
    ) -> EvaluatedStep:
        step_id = evaluation.selection.fill_evaluation_step_id
        selection = evaluation.selection
        fill = evaluation.fill
        order_events = evaluation.order_events

        _insert_or_compare(
            connection,
            "live_paper_step_terminal_claims",
            ("fill_evaluation_step_id", "variant", "resolution_id", "resolved_at"),
            (
                step_id,
                PaperStepResolutionVariant.MARKET_SELECTED.value,
                selection.market_observation_selection_id,
                _dt(evaluated_at),
            ),
            ("fill_evaluation_step_id",),
            "Step terminal claim already persisted with different content",
        )
        _insert_or_compare(
            connection,
            "live_paper_market_observation_selections",
            *_selection_row(selection),
            ("market_observation_selection_id",),
            "market observation selection already persisted with different content",
        )
        _insert_or_compare(
            connection,
            "live_paper_fills",
            *_fill_row(fill),
            ("paper_fill_id",),
            "Paper Fill already persisted with different content",
        )
        for event in order_events:
            _insert_or_compare(
                connection,
                "live_paper_order_events",
                *_order_event_row(event),
                ("paper_order_event_id",),
                "Paper order event already persisted with different content",
            )

        lineage = order.intent_lineage
        application_kind = (
            PaperPositionApplicationKind.ENTRY
            if lineage.intent_kind is PaperIntentKind.ENTRY
            else PaperPositionApplicationKind.REDUCE_ONLY
        )
        position_id = lineage.paper_position_id
        prior_applications = self._hydrate_ordered_applications(connection, position_id)
        pre_transaction_open_pairs = self._open_pairs_for_account(
            connection, order.paper_account_id
        )

        existing_position = self._hydrate_position(connection, position_id)
        if application_kind is PaperPositionApplicationKind.ENTRY:
            if existing_position is None:
                position_record = PaperPositionRecord(
                    position_id,
                    _PAPER_POSITION_CONTRACT_VERSION,
                    order.paper_account_id,
                    order.paper_order_id,
                    order.pair,
                    _position_side_for(order.side),
                    evaluated_at,
                )
                _insert_or_compare(
                    connection,
                    "live_paper_positions",
                    *_position_row(position_record),
                    ("paper_position_id",),
                    "Paper position already persisted with different content",
                )
                existing_position = position_record
            resolved_position_side = existing_position.position_side
        else:
            if existing_position is None:
                raise PaperPersistenceIntegrityError(
                    "a REDUCE_ONLY application requires an existing Paper position"
                )
            resolved_position_side = existing_position.position_side

        open_quantity_after, realized_pnl_amount = compute_position_application_fields(
            prior_applications,
            position_side=resolved_position_side,
            application_kind=application_kind,
            quantity=fill.fill_quantity,
            price=fill.fill_price,
        )
        application = PaperPositionFillApplication.create(
            paper_position_id=position_id,
            paper_order_id=order.paper_order_id,
            paper_fill_id=fill.paper_fill_id,
            application_kind=application_kind,
            quantity=fill.fill_quantity,
            price=fill.fill_price,
            open_quantity_after=open_quantity_after,
            realized_pnl_amount=realized_pnl_amount,
            created_at=evaluated_at,
        )
        _insert_or_compare_returning_seq(
            connection,
            "live_paper_position_fill_applications",
            *_application_row(application),
            ("paper_position_fill_application_id",),
            "position fill application already persisted with different content",
            seq_column="application_seq",
        )

        ledger_entry: PaperLedgerEntry | None = None
        if application_kind is PaperPositionApplicationKind.REDUCE_ONLY:
            assert realized_pnl_amount is not None
            ledger_entry = PaperLedgerEntry.create(
                paper_account_id=order.paper_account_id,
                paper_position_id=position_id,
                entry_kind=PaperLedgerEntryKind.REALIZED_PNL,
                settlement_currency=_JPY,
                amount=realized_pnl_amount,
                source_evidence_kind="PAPER_POSITION_FILL_APPLICATION",
                source_evidence_id=application.paper_position_fill_application_id,
                formula_version=PAPER_REALIZED_PNL_V1,
                created_at=evaluated_at,
            )
            _insert_or_compare_returning_seq(
                connection,
                "live_paper_ledger_entries",
                *_ledger_entry_row(ledger_entry),
                ("ledger_entry_id",),
                "ledger entry already persisted with different content",
                seq_column="ledger_entry_seq",
            )

        self._authenticate_mark_observations(connection, mark_observations)
        coverage_set = paper_account_mark_set_required_coverage_v1(
            pre_transaction_open_pairs=pre_transaction_open_pairs, order_pair=order.pair
        )
        mark_set = PaperAccountMarkSet.create(
            mark_observations, coverage_set=coverage_set, bounding_instant=evaluated_at
        )
        bootstrap = self._hydrate_account_bootstrap(connection, order.paper_account_id)
        assert bootstrap is not None
        position_snapshot, account_snapshot = self._write_snapshots(
            connection,
            bootstrap=bootstrap,
            touched_position_id=position_id,
            mark_set=mark_set,
            evaluated_at=evaluated_at,
        )

        reservation_consumption: ReservationConsumptionEvidence | None = None
        reservation_release: ReservationReleaseEvidence | None = None
        outcome = StepResolutionOutcome.T3B if len(order_events) == 2 else StepResolutionOutcome.T3A
        idempotency_key = lineage.source_intent_idempotency_key
        if lineage.intent_kind is PaperIntentKind.ORDINARY_CLOSE:
            if self._reservation_release_exists(connection, order.paper_order_id):
                raise PaperPersistenceIntegrityError(
                    "reservation consumption is not authorised after a release "
                    "already exists for this order"
                )
            reservation_consumption = ReservationConsumptionEvidence.create(
                close_intent_idempotency_key=idempotency_key,
                paper_order_id=order.paper_order_id,
                paper_fill_id=fill.paper_fill_id,
                consumed_quantity=fill.fill_quantity,
                created_at=evaluated_at,
            )
            _insert_or_compare(
                connection,
                "live_paper_reservation_consumptions",
                *_consumption_row(reservation_consumption),
                ("consumption_id",),
                "reservation consumption already persisted with different content",
            )
            consumed_total = self._reservation_consumed_total(connection, idempotency_key)
            intent_quantity = self._m2d_intent_quantity(connection, idempotency_key)
            if consumed_total > intent_quantity:
                raise PaperPersistenceIntegrityError(
                    "reservation consumed_total exceeds intent.quantity"
                )
            if outcome is StepResolutionOutcome.T3B:
                with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
                    released_quantity = intent_quantity - consumed_total
                if released_quantity <= 0:
                    raise PaperPersistenceIntegrityError(
                        "reservation release must be strictly positive"
                    )
                reservation_release = ReservationReleaseEvidence.create(
                    close_intent_idempotency_key=idempotency_key,
                    paper_order_id=order.paper_order_id,
                    terminal_order_state=order_events[-1].state,
                    released_quantity=released_quantity,
                    created_at=evaluated_at,
                )
                _insert_or_compare(
                    connection,
                    "live_paper_reservation_releases",
                    *_release_row(reservation_release),
                    ("release_id",),
                    "reservation release already persisted with different content",
                )

        return EvaluatedStep(
            outcome,
            order_events,
            None,
            selection,
            fill,
            None,
            application,
            ledger_entry,
            position_snapshot,
            account_snapshot,
            reservation_consumption,
            reservation_release,
        )

    def _apply_no_market(
        self,
        connection: sqlite3.Connection,
        *,
        order: PaperOrder,
        evaluation: NoMarketStepEvaluation,
        evaluated_at: datetime,
    ) -> EvaluatedStep:
        outcome_obj = evaluation.outcome
        _insert_or_compare(
            connection,
            "live_paper_step_terminal_claims",
            ("fill_evaluation_step_id", "variant", "resolution_id", "resolved_at"),
            (
                outcome_obj.fill_evaluation_step_id,
                PaperStepResolutionVariant.NO_MARKET.value,
                outcome_obj.no_market_outcome_id,
                _dt(evaluated_at),
            ),
            ("fill_evaluation_step_id",),
            "Step terminal claim already persisted with different content",
        )
        _insert_or_compare(
            connection,
            "live_paper_no_market_outcomes",
            *_no_market_outcome_row(outcome_obj),
            ("no_market_outcome_id",),
            "no-market outcome already persisted with different content",
        )
        for event in evaluation.order_events:
            _insert_or_compare(
                connection,
                "live_paper_order_events",
                *_order_event_row(event),
                ("paper_order_event_id",),
                "Paper order event already persisted with different content",
            )

        reservation_release: ReservationReleaseEvidence | None = None
        lineage = order.intent_lineage
        if lineage.intent_kind is PaperIntentKind.ORDINARY_CLOSE:
            idempotency_key = lineage.source_intent_idempotency_key
            consumed_total = self._reservation_consumed_total(connection, idempotency_key)
            intent_quantity = self._m2d_intent_quantity(connection, idempotency_key)
            with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
                released_quantity = intent_quantity - consumed_total
            if released_quantity <= 0:
                raise PaperPersistenceIntegrityError(
                    "reservation release must be strictly positive"
                )
            reservation_release = ReservationReleaseEvidence.create(
                close_intent_idempotency_key=idempotency_key,
                paper_order_id=order.paper_order_id,
                terminal_order_state=evaluation.order_events[0].state,
                released_quantity=released_quantity,
                created_at=evaluated_at,
            )
            _insert_or_compare(
                connection,
                "live_paper_reservation_releases",
                *_release_row(reservation_release),
                ("release_id",),
                "reservation release already persisted with different content",
            )

        return EvaluatedStep(
            StepResolutionOutcome.T3C,
            evaluation.order_events,
            None,
            None,
            None,
            outcome_obj,
            None,
            None,
            None,
            None,
            None,
            reservation_release,
        )

    def _replay_resolved_step(
        self, connection: sqlite3.Connection, order: PaperOrder, claim_row: sqlite3.Row
    ) -> EvaluatedStep:
        variant = PaperStepResolutionVariant(claim_row["variant"])
        if variant is PaperStepResolutionVariant.MARKET_SELECTED:
            selection_row = connection.execute(
                "SELECT * FROM live_paper_market_observation_selections "
                "WHERE market_observation_selection_id = ?",
                (claim_row["resolution_id"],),
            ).fetchone()
            assert selection_row is not None
            selection = _hydrate_selection(selection_row)
            fill_row = connection.execute(
                "SELECT * FROM live_paper_fills WHERE market_observation_selection_id = ?",
                (selection.market_observation_selection_id,),
            ).fetchone()
            assert fill_row is not None
            fill = _hydrate_fill(fill_row)
            event_rows = connection.execute(
                "SELECT * FROM live_paper_order_events WHERE source_evidence_id = ? "
                "ORDER BY event_ordinal ASC",
                (fill.paper_fill_id,),
            ).fetchall()
            events = tuple(_hydrate_order_event(row) for row in event_rows)
            outcome = StepResolutionOutcome.T3B if len(events) == 2 else StepResolutionOutcome.T3A
            application_row = connection.execute(
                "SELECT * FROM live_paper_position_fill_applications WHERE paper_fill_id = ?",
                (fill.paper_fill_id,),
            ).fetchone()
            application = None if application_row is None else _hydrate_application(application_row)
            ledger_entry = None
            if application is not None:
                ledger_row = connection.execute(
                    "SELECT * FROM live_paper_ledger_entries "
                    "WHERE entry_kind = 'REALIZED_PNL' AND source_evidence_id = ?",
                    (application.paper_position_fill_application_id,),
                ).fetchone()
                ledger_entry = None if ledger_row is None else _hydrate_ledger_entry(ledger_row)
            consumption_row = connection.execute(
                "SELECT * FROM live_paper_reservation_consumptions WHERE paper_fill_id = ?",
                (fill.paper_fill_id,),
            ).fetchone()
            consumption = None if consumption_row is None else _hydrate_consumption(consumption_row)
            release_row = connection.execute(
                "SELECT * FROM live_paper_reservation_releases WHERE paper_order_id = ?",
                (order.paper_order_id,),
            ).fetchone()
            release = None if release_row is None else _hydrate_release(release_row)
            return EvaluatedStep(
                outcome,
                events,
                None,
                selection,
                fill,
                None,
                application,
                ledger_entry,
                None,
                None,
                consumption,
                release,
            )
        outcome_row = connection.execute(
            "SELECT * FROM live_paper_no_market_outcomes WHERE no_market_outcome_id = ?",
            (claim_row["resolution_id"],),
        ).fetchone()
        assert outcome_row is not None
        outcome_obj = _hydrate_no_market_outcome(outcome_row)
        event_rows = connection.execute(
            "SELECT * FROM live_paper_order_events "
            "WHERE source_evidence_id = ? ORDER BY event_ordinal ASC",
            (outcome_obj.no_market_outcome_id,),
        ).fetchall()
        events = tuple(_hydrate_order_event(row) for row in event_rows)
        release_row = connection.execute(
            "SELECT * FROM live_paper_reservation_releases WHERE paper_order_id = ?",
            (order.paper_order_id,),
        ).fetchone()
        release = None if release_row is None else _hydrate_release(release_row)
        return EvaluatedStep(
            StepResolutionOutcome.T3C,
            events,
            None,
            None,
            None,
            outcome_obj,
            None,
            None,
            None,
            None,
            None,
            release,
        )

    # -- T5 -----------------------------------------------------------------

    def accrue_or_skip_swap(
        self,
        *,
        paper_position_snapshot_id: str,
        evidence: OperationalSwapEvidence | None,
        rollover_date: date,
        policy: PaperSwapAccrualPolicy,
        mark_observations: tuple[PaperMarketObservation, ...],
        resolved_at: datetime,
    ) -> SwapRolloverResult:
        if type(policy) is not PaperSwapAccrualPolicy:
            raise TypeError("policy must be exact PaperSwapAccrualPolicy")
        PaperSwapAccrualPolicy.__post_init__(policy)
        if type(rollover_date) is not date:
            raise TypeError("rollover_date must be exact datetime.date")
        if evidence is not None and type(evidence) is not OperationalSwapEvidence:
            raise TypeError("evidence must be exact OperationalSwapEvidence or None")
        if type(mark_observations) is not tuple:
            raise TypeError("mark_observations must be exact tuple")
        _require_utc_datetime(resolved_at, "resolved_at")

        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                snapshot_row = connection.execute(
                    "SELECT * FROM live_paper_position_snapshots "
                    "WHERE paper_position_snapshot_id = ?",
                    (paper_position_snapshot_id,),
                ).fetchone()
                if snapshot_row is None:
                    raise PaperPersistenceIntegrityError(
                        "paper_position_snapshot_id is not persisted"
                    )
                snapshot = _hydrate_position_snapshot(snapshot_row)
                position = self._hydrate_position(connection, snapshot.paper_position_id)
                if position is None:
                    raise PaperPersistenceIntegrityError(
                        "swap accrual snapshot references a position that is not persisted"
                    )
                account_id = position.paper_account_id

                floor = self._max_scan_set_instant(connection, account_id)
                if floor is not None and resolved_at < floor:
                    raise PaperPersistenceIntegrityError(
                        "resolved_at regressed before the T5/T6/T7 non-regression scan set"
                    )

                superseding_application = connection.execute(
                    "SELECT 1 FROM live_paper_position_fill_applications "
                    "WHERE paper_position_id = ? AND application_seq > ?",
                    (snapshot.paper_position_id, snapshot.highest_application_seq),
                ).fetchone()
                if superseding_application is not None:
                    raise PaperPersistenceIntegrityError(
                        "paper_position_snapshot_id is superseded by a later application"
                    )
                superseding_ledger = connection.execute(
                    "SELECT 1 FROM live_paper_ledger_entries "
                    "WHERE paper_position_id = ? "
                    "AND entry_kind IN ('SWAP_ACCRUAL', 'SWAP_ACCRUAL_CORRECTION') "
                    "AND ledger_entry_seq > ?",
                    (snapshot.paper_position_id, snapshot.highest_ledger_entry_seq),
                ).fetchone()
                if superseding_ledger is not None:
                    raise PaperPersistenceIntegrityError(
                        "paper_position_snapshot_id is superseded by a later swap ledger entry"
                    )

                claim_row = connection.execute(
                    "SELECT * FROM live_paper_swap_rollover_claims "
                    "WHERE paper_position_id = ? AND rollover_date = ?",
                    (position.paper_position_id, rollover_date.isoformat()),
                ).fetchone()
                if claim_row is not None:
                    result = self._replay_swap_rollover(connection, claim_row)
                    connection.commit()
                    return result

                evidence_or_non = evaluate_paper_swap_accrual(
                    evidence=evidence,
                    paper_position_id=position.paper_position_id,
                    paper_position_snapshot_id=paper_position_snapshot_id,
                    position_pair=position.pair,
                    position_side=position.position_side,
                    open_quantity=snapshot.open_quantity,
                    policy=policy,
                    rollover_date=rollover_date,
                    created_at=resolved_at,
                )
                if type(evidence_or_non) is PaperSwapAccrual:
                    accrual = evidence_or_non
                    _insert_or_compare(
                        connection,
                        "live_paper_swap_rollover_claims",
                        (
                            "paper_position_id",
                            "rollover_date",
                            "variant",
                            "evidence_id",
                            "resolved_at",
                        ),
                        (
                            position.paper_position_id,
                            rollover_date.isoformat(),
                            "ACCRUED",
                            accrual.paper_swap_accrual_id,
                            _dt(resolved_at),
                        ),
                        ("paper_position_id", "rollover_date"),
                        "swap rollover claim already persisted with different content",
                    )
                    _insert_or_compare(
                        connection,
                        "live_paper_swap_accruals",
                        *_swap_accrual_row(accrual),
                        ("paper_swap_accrual_id",),
                        "swap accrual already persisted with different content",
                    )
                    ledger_entry = PaperLedgerEntry.create(
                        paper_account_id=account_id,
                        paper_position_id=position.paper_position_id,
                        entry_kind=PaperLedgerEntryKind.SWAP_ACCRUAL,
                        settlement_currency=_JPY,
                        amount=accrual.amount,
                        source_evidence_kind="PAPER_SWAP_ACCRUAL",
                        source_evidence_id=accrual.paper_swap_accrual_id,
                        formula_version=PAPER_SWAP_ACCRUAL_V1,
                        created_at=resolved_at,
                    )
                    _insert_or_compare_returning_seq(
                        connection,
                        "live_paper_ledger_entries",
                        *_ledger_entry_row(ledger_entry),
                        ("ledger_entry_id",),
                        "ledger entry already persisted with different content",
                        seq_column="ledger_entry_seq",
                    )

                    self._authenticate_mark_observations(connection, mark_observations)
                    coverage_set = self._open_pairs_for_account(connection, account_id)
                    mark_set = PaperAccountMarkSet.create(
                        mark_observations, coverage_set=coverage_set, bounding_instant=resolved_at
                    )
                    bootstrap = self._hydrate_account_bootstrap(connection, account_id)
                    assert bootstrap is not None
                    position_snapshot, account_snapshot = self._write_snapshots(
                        connection,
                        bootstrap=bootstrap,
                        touched_position_id=position.paper_position_id,
                        mark_set=mark_set,
                        evaluated_at=resolved_at,
                    )
                    connection.commit()
                    return SwapRolloverResult(
                        PaperSwapAccrualOutcome.ACCRUED,
                        accrual,
                        None,
                        ledger_entry,
                        position_snapshot,
                        account_snapshot,
                    )

                non_accrual = evidence_or_non
                assert type(non_accrual) is PaperSwapNonAccrual
                _insert_or_compare(
                    connection,
                    "live_paper_swap_rollover_claims",
                    ("paper_position_id", "rollover_date", "variant", "evidence_id", "resolved_at"),
                    (
                        position.paper_position_id,
                        rollover_date.isoformat(),
                        "NOT_ACCRUED",
                        non_accrual.paper_swap_non_accrual_id,
                        _dt(resolved_at),
                    ),
                    ("paper_position_id", "rollover_date"),
                    "swap rollover claim already persisted with different content",
                )
                _insert_or_compare(
                    connection,
                    "live_paper_swap_non_accruals",
                    *_swap_non_accrual_row(non_accrual),
                    ("paper_swap_non_accrual_id",),
                    "swap non-accrual already persisted with different content",
                )
                connection.commit()
                return SwapRolloverResult(non_accrual.outcome, None, non_accrual, None, None, None)
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def _replay_swap_rollover(
        self, connection: sqlite3.Connection, claim_row: sqlite3.Row
    ) -> SwapRolloverResult:
        if claim_row["variant"] == "ACCRUED":
            accrual_row = connection.execute(
                "SELECT * FROM live_paper_swap_accruals WHERE paper_swap_accrual_id = ?",
                (claim_row["evidence_id"],),
            ).fetchone()
            assert accrual_row is not None
            accrual = _hydrate_swap_accrual(accrual_row)
            ledger_row = connection.execute(
                "SELECT * FROM live_paper_ledger_entries "
                "WHERE entry_kind = 'SWAP_ACCRUAL' AND source_evidence_id = ?",
                (accrual.paper_swap_accrual_id,),
            ).fetchone()
            ledger_entry = None if ledger_row is None else _hydrate_ledger_entry(ledger_row)
            return SwapRolloverResult(
                PaperSwapAccrualOutcome.ACCRUED, accrual, None, ledger_entry, None, None
            )
        non_accrual_row = connection.execute(
            "SELECT * FROM live_paper_swap_non_accruals WHERE paper_swap_non_accrual_id = ?",
            (claim_row["evidence_id"],),
        ).fetchone()
        assert non_accrual_row is not None
        non_accrual = _hydrate_swap_non_accrual(non_accrual_row)
        return SwapRolloverResult(non_accrual.outcome, None, non_accrual, None, None, None)

    # -- T6 -----------------------------------------------------------------

    def correct_swap_accrual(
        self,
        *,
        corrected_accrual_id: str,
        chain_ordinal: int,
        predecessor_correction_id: str | None,
        replacement_amount: Decimal,
        correction_reason: str,
        swap_evidence_id: str,
        mark_observations: tuple[PaperMarketObservation, ...],
        resolved_at: datetime,
    ) -> SwapCorrectionResult:
        _require_utc_datetime(resolved_at, "resolved_at")
        if type(mark_observations) is not tuple:
            raise TypeError("mark_observations must be exact tuple")

        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                accrual_row = connection.execute(
                    "SELECT * FROM live_paper_swap_accruals WHERE paper_swap_accrual_id = ?",
                    (corrected_accrual_id,),
                ).fetchone()
                if accrual_row is None:
                    raise PaperPersistenceIntegrityError("corrected_accrual_id is not persisted")
                accrual = _hydrate_swap_accrual(accrual_row)
                position = self._hydrate_position(connection, accrual.paper_position_id)
                if position is None:
                    raise PaperPersistenceIntegrityError(
                        "swap accrual correction references a position that is not persisted"
                    )
                account_id = position.paper_account_id

                floor = self._max_scan_set_instant(connection, account_id)
                if floor is not None and resolved_at < floor:
                    raise PaperPersistenceIntegrityError(
                        "resolved_at regressed before the T5/T6/T7 non-regression scan set"
                    )

                chain_rows = connection.execute(
                    "SELECT * FROM live_paper_swap_accrual_corrections "
                    "WHERE corrected_accrual_id = ? ORDER BY chain_ordinal ASC",
                    (corrected_accrual_id,),
                ).fetchall()
                existing_chain = tuple(_hydrate_swap_correction(row) for row in chain_rows)
                # Validate against the chain strictly before this ordinal, so a byte-identical
                # replay of the already-persisted last correction re-validates cleanly instead of
                # perceiving its own already-committed row as an out-of-order successor.
                prior_chain = tuple(c for c in existing_chain if c.chain_ordinal < chain_ordinal)

                correction = next_swap_accrual_correction(
                    original_accrual=accrual,
                    existing_chain=prior_chain,
                    chain_ordinal=chain_ordinal,
                    predecessor_correction_id=predecessor_correction_id,
                    replacement_amount=replacement_amount,
                    correction_reason=correction_reason,
                    swap_evidence_id=swap_evidence_id,
                    created_at=resolved_at,
                )
                _insert_or_compare_returning_seq(
                    connection,
                    "live_paper_swap_accrual_corrections",
                    *_swap_correction_row(correction),
                    ("correction_id",),
                    "swap accrual correction already persisted with different content",
                    seq_column="correction_seq",
                )

                ledger_entry = PaperLedgerEntry.create(
                    paper_account_id=account_id,
                    paper_position_id=position.paper_position_id,
                    entry_kind=PaperLedgerEntryKind.SWAP_ACCRUAL_CORRECTION,
                    settlement_currency=_JPY,
                    amount=correction.delta_amount,
                    source_evidence_kind="PAPER_SWAP_ACCRUAL_CORRECTION",
                    source_evidence_id=correction.correction_id,
                    formula_version=PAPER_SWAP_ACCRUAL_V1,
                    created_at=resolved_at,
                )
                _insert_or_compare_returning_seq(
                    connection,
                    "live_paper_ledger_entries",
                    *_ledger_entry_row(ledger_entry),
                    ("ledger_entry_id",),
                    "ledger entry already persisted with different content",
                    seq_column="ledger_entry_seq",
                )

                self._authenticate_mark_observations(connection, mark_observations)
                coverage_set = self._open_pairs_for_account(connection, account_id)
                mark_set = PaperAccountMarkSet.create(
                    mark_observations, coverage_set=coverage_set, bounding_instant=resolved_at
                )
                bootstrap = self._hydrate_account_bootstrap(connection, account_id)
                assert bootstrap is not None
                position_snapshot, account_snapshot = self._write_snapshots(
                    connection,
                    bootstrap=bootstrap,
                    touched_position_id=position.paper_position_id,
                    mark_set=mark_set,
                    evaluated_at=resolved_at,
                )
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return SwapCorrectionResult(correction, ledger_entry, position_snapshot, account_snapshot)

    # -- T7 -----------------------------------------------------------------

    def reconcile_account(
        self, *, paper_account_id: str, resolved_at: datetime
    ) -> PaperReconciliationResult:
        if type(paper_account_id) is not str or not paper_account_id.strip():
            raise ValueError("paper_account_id must be a non-blank exact str")
        _require_utc_datetime(resolved_at, "resolved_at")

        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                bootstrap = self._hydrate_account_bootstrap(connection, paper_account_id)
                if bootstrap is None:
                    raise PaperPersistenceIntegrityError(
                        "paper_account_id is not a persisted account bootstrap"
                    )

                floor = self._max_scan_set_instant(connection, paper_account_id)
                if floor is not None and resolved_at < floor:
                    raise PaperPersistenceIntegrityError(
                        "resolved_at regressed before the T5/T6/T7 non-regression scan set"
                    )

                result = self._rebuild_and_compare_account(
                    connection, paper_account_id, bootstrap, resolved_at
                )
                _insert_or_compare(
                    connection,
                    "live_paper_reconciliation_results",
                    *_reconciliation_row(result),
                    ("reconciliation_result_id",),
                    "reconciliation result already persisted with different content",
                )
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return result

    def _rebuild_and_compare_account(
        self,
        connection: sqlite3.Connection,
        paper_account_id: str,
        bootstrap: PaperAccountBootstrap,
        resolved_at: datetime,
    ) -> PaperReconciliationResult:
        position_rows = connection.execute(
            "SELECT * FROM live_paper_positions "
            "WHERE paper_account_id = ? ORDER BY paper_position_id ASC",
            (paper_account_id,),
        ).fetchall()
        positions = tuple(_hydrate_position_record(row) for row in position_rows)
        reconciled_position_ids = tuple(sorted(p.paper_position_id for p in positions))

        mismatched: dict[PaperReconciledRecordKind, set[str]] = {
            kind: set() for kind in PaperReconciledRecordKind
        }

        orders_by_id: dict[str, PaperOrder] = {}
        fills_by_id: dict[str, PaperFill] = {}
        applications_by_id: dict[str, PaperPositionFillApplication] = {}
        applications_by_position: dict[str, tuple[PaperPositionFillApplication, ...]] = {}
        position_accounts: dict[str, str] = {}
        highest_application_seq = 0

        for position in positions:
            position_accounts[position.paper_position_id] = position.paper_account_id
            applications = self._hydrate_ordered_applications(
                connection, position.paper_position_id
            )
            applications_by_position[position.paper_position_id] = applications
            highest_application_seq = max(
                highest_application_seq,
                self._max_application_seq(connection, position.paper_position_id),
            )
            for application in applications:
                applications_by_id[application.paper_position_fill_application_id] = application
                if application.paper_order_id not in orders_by_id:
                    order_row = connection.execute(
                        "SELECT * FROM live_paper_orders WHERE paper_order_id = ?",
                        (application.paper_order_id,),
                    ).fetchone()
                    if order_row is not None:
                        orders_by_id[application.paper_order_id] = _hydrate_order_row(order_row)
                fill_row = connection.execute(
                    "SELECT * FROM live_paper_fills WHERE paper_fill_id = ?",
                    (application.paper_fill_id,),
                ).fetchone()
                if fill_row is not None:
                    fills_by_id[application.paper_fill_id] = _hydrate_fill(fill_row)
            mismatched[PaperReconciledRecordKind.POSITION_FILL_APPLICATION].update(
                rebuild_position_fill_applications(applications, fills_by_id, orders_by_id)
            )

        ledger_rows = connection.execute(
            "SELECT * FROM live_paper_ledger_entries "
            "WHERE paper_account_id = ? ORDER BY ledger_entry_seq ASC",
            (paper_account_id,),
        ).fetchall()
        ledger_entries = tuple(_hydrate_ledger_entry(row) for row in ledger_rows)
        highest_ledger_entry_seq = max(
            (int(row["ledger_entry_seq"]) for row in ledger_rows), default=0
        )

        realized_pnl_sources: dict[str, PaperPositionFillApplication] = {}
        swap_accruals_by_id: dict[str, PaperSwapAccrual] = {}
        swap_corrections_by_id: dict[str, PaperSwapAccrualCorrection] = {}
        for entry in ledger_entries:
            if entry.entry_kind is PaperLedgerEntryKind.REALIZED_PNL:
                realized_pnl_application = applications_by_id.get(entry.source_evidence_id)
                if realized_pnl_application is not None:
                    realized_pnl_sources[entry.source_evidence_id] = realized_pnl_application
            elif entry.entry_kind is PaperLedgerEntryKind.SWAP_ACCRUAL:
                row = connection.execute(
                    "SELECT * FROM live_paper_swap_accruals WHERE paper_swap_accrual_id = ?",
                    (entry.source_evidence_id,),
                ).fetchone()
                if row is not None:
                    swap_accruals_by_id[entry.source_evidence_id] = _hydrate_swap_accrual(row)
            else:
                row = connection.execute(
                    "SELECT * FROM live_paper_swap_accrual_corrections WHERE correction_id = ?",
                    (entry.source_evidence_id,),
                ).fetchone()
                if row is not None:
                    correction = _hydrate_swap_correction(row)
                    swap_corrections_by_id[entry.source_evidence_id] = correction
                    accrual_row = connection.execute(
                        "SELECT * FROM live_paper_swap_accruals WHERE paper_swap_accrual_id = ?",
                        (correction.corrected_accrual_id,),
                    ).fetchone()
                    if accrual_row is not None:
                        swap_accruals_by_id.setdefault(
                            correction.corrected_accrual_id, _hydrate_swap_accrual(accrual_row)
                        )
            if entry.paper_position_id not in position_accounts:
                row = connection.execute(
                    "SELECT paper_account_id FROM live_paper_positions WHERE paper_position_id = ?",
                    (entry.paper_position_id,),
                ).fetchone()
                if row is not None:
                    position_accounts[entry.paper_position_id] = row["paper_account_id"]
        for accrual in swap_accruals_by_id.values():
            if accrual.paper_position_id not in position_accounts:
                row = connection.execute(
                    "SELECT paper_account_id FROM live_paper_positions WHERE paper_position_id = ?",
                    (accrual.paper_position_id,),
                ).fetchone()
                if row is not None:
                    position_accounts[accrual.paper_position_id] = row["paper_account_id"]

        mismatched[PaperReconciledRecordKind.LEDGER_ENTRY].update(
            rebuild_ledger_entries(
                ledger_entries,
                realized_pnl_sources=realized_pnl_sources,
                swap_accruals=swap_accruals_by_id,
                swap_corrections=swap_corrections_by_id,
                position_accounts=position_accounts,
            )
        )

        position_snapshot_rows = connection.execute(
            "SELECT * FROM live_paper_position_snapshots WHERE paper_account_id = ?",
            (paper_account_id,),
        ).fetchall()
        for row in position_snapshot_rows:
            snapshot = _hydrate_position_snapshot(row)
            position = next(
                p for p in positions if p.paper_position_id == snapshot.paper_position_id
            )
            applications = tuple(
                a
                for a in applications_by_position[snapshot.paper_position_id]
                if _application_seq_of(connection, a.paper_position_fill_application_id)
                <= snapshot.highest_application_seq
            )
            swap_entries = tuple(
                e
                for e in self._hydrate_swap_ledger_entries(connection, snapshot.paper_position_id)
                if _ledger_entry_seq_of(connection, e.ledger_entry_id)
                <= snapshot.highest_ledger_entry_seq
            )
            matched = rebuild_position_snapshot(
                snapshot,
                paper_account_id=paper_account_id,
                pair=position.pair,
                position_side=position.position_side,
                applications=applications,
                swap_ledger_entries=swap_entries,
                highest_application_seq=snapshot.highest_application_seq,
                highest_ledger_entry_seq=snapshot.highest_ledger_entry_seq,
            )
            if not matched:
                mismatched[PaperReconciledRecordKind.POSITION_SNAPSHOT].add(
                    snapshot.paper_position_snapshot_id
                )

        order_rows_for_account = connection.execute(
            "SELECT paper_order_id FROM live_paper_orders WHERE paper_account_id = ?",
            (paper_account_id,),
        ).fetchall()
        highest_order_event_seq = 0
        for order_row in order_rows_for_account:
            _, max_seq = self._hydrate_order_events_with_seq(
                connection, order_row["paper_order_id"]
            )
            highest_order_event_seq = max(highest_order_event_seq, max_seq)

        account_snapshot_rows = connection.execute(
            "SELECT * FROM live_paper_account_snapshots WHERE paper_account_id = ?",
            (paper_account_id,),
        ).fetchall()
        for row in account_snapshot_rows:
            account_snapshot = _hydrate_account_snapshot(row)
            positions_input = []
            for position in positions:
                applications = tuple(
                    a
                    for a in applications_by_position[position.paper_position_id]
                    if _application_seq_of(connection, a.paper_position_fill_application_id)
                    <= account_snapshot.highest_application_seq
                )
                positions_input.append(
                    PaperAccountSnapshotPositionInput(
                        position.paper_position_id,
                        position.pair,
                        position.position_side,
                        applications,
                    )
                )
            entries_for_snapshot = tuple(
                e
                for e in ledger_entries
                if _ledger_entry_seq_of(connection, e.ledger_entry_id)
                <= account_snapshot.highest_ledger_entry_seq
            )
            observations_by_pair: dict[str, PaperMarketObservation] = {}
            for market_observation_id in account_snapshot.mark_observation_ids:
                obs_row = connection.execute(
                    "SELECT * FROM live_paper_market_observations WHERE market_observation_id = ?",
                    (market_observation_id,),
                ).fetchone()
                if obs_row is not None:
                    observation = _hydrate_market_observation(obs_row)
                    observations_by_pair[observation.pair.symbol] = observation
            order_events_by_order: dict[str, tuple[PaperOrderEvent, ...]] = {}
            for order_row in order_rows_for_account:
                events, _seq = self._hydrate_order_events_with_seq(
                    connection, order_row["paper_order_id"]
                )
                truncated = tuple(
                    event
                    for event in events
                    if _order_event_seq_of(connection, event.paper_order_event_id)
                    <= account_snapshot.highest_order_event_seq
                )
                if truncated:
                    order_events_by_order[order_row["paper_order_id"]] = truncated
            matched = rebuild_account_snapshot(
                account_snapshot,
                bootstrap=bootstrap,
                positions=positions_input,
                ledger_entries=entries_for_snapshot,
                observations_by_pair=observations_by_pair,
                order_events_by_order=order_events_by_order,
                highest_application_seq=account_snapshot.highest_application_seq,
                highest_ledger_entry_seq=account_snapshot.highest_ledger_entry_seq,
                highest_order_event_seq=account_snapshot.highest_order_event_seq,
            )
            if not matched:
                mismatched[PaperReconciledRecordKind.ACCOUNT_SNAPSHOT].add(
                    account_snapshot.paper_account_snapshot_id
                )

        mismatched_record_kinds = tuple(
            sorted((kind for kind, ids in mismatched.items() if ids), key=lambda k: k.value)
        )
        mismatched_record_ids = tuple(sorted({item for ids in mismatched.values() for item in ids}))

        return PaperReconciliationResult.create(
            paper_account_id=paper_account_id,
            reconciled_position_ids=reconciled_position_ids,
            highest_application_seq=highest_application_seq,
            highest_ledger_entry_seq=highest_ledger_entry_seq,
            highest_order_event_seq=highest_order_event_seq,
            mismatched_record_kinds=mismatched_record_kinds,
            mismatched_record_ids=mismatched_record_ids,
            created_at=resolved_at,
        )

    # -- Shared snapshot writer (T3a/T3b, T5 accrual, T6) --------------------

    def _write_snapshots(
        self,
        connection: sqlite3.Connection,
        *,
        bootstrap: PaperAccountBootstrap,
        touched_position_id: str,
        mark_set: PaperAccountMarkSet,
        evaluated_at: datetime,
    ) -> tuple[PaperPositionSnapshot, PaperAccountSnapshot]:
        position = self._hydrate_position(connection, touched_position_id)
        assert position is not None
        applications = self._hydrate_ordered_applications(connection, touched_position_id)
        highest_application_seq_for_position = self._max_application_seq(
            connection, touched_position_id
        )
        swap_entries_for_position = self._hydrate_swap_ledger_entries(
            connection, touched_position_id
        )
        highest_ledger_entry_seq_for_position = self._max_ledger_entry_seq_for_position(
            connection, touched_position_id
        )

        open_quantity = project_paper_position_open_quantity(applications)
        entry_applications = tuple(
            a for a in applications if a.application_kind is PaperPositionApplicationKind.ENTRY
        )
        average_entry_price = paper_weighted_average_entry_price_v1(entry_applications)
        with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
            realized_pnl_total = Decimal(0)
            for application in applications:
                if application.realized_pnl_amount is not None:
                    realized_pnl_total = realized_pnl_total + application.realized_pnl_amount
            accrued_swap_total = Decimal(0)
            for swap_entry in swap_entries_for_position:
                accrued_swap_total = accrued_swap_total + swap_entry.amount

        position_snapshot = PaperPositionSnapshot.create(
            paper_account_id=position.paper_account_id,
            paper_position_id=touched_position_id,
            pair=position.pair,
            position_side=position.position_side,
            open_quantity=open_quantity,
            average_entry_price=average_entry_price,
            realized_pnl_total=realized_pnl_total,
            accrued_swap_total=accrued_swap_total,
            highest_application_seq=highest_application_seq_for_position,
            highest_ledger_entry_seq=highest_ledger_entry_seq_for_position,
            created_at=evaluated_at,
        )
        _insert_or_compare(
            connection,
            "live_paper_position_snapshots",
            *_position_snapshot_row(position_snapshot),
            ("paper_position_snapshot_id",),
            "position snapshot already persisted with different content",
        )

        account_id = bootstrap.paper_account_id
        position_rows = connection.execute(
            "SELECT * FROM live_paper_positions WHERE paper_account_id = ?", (account_id,)
        ).fetchall()
        positions_input: list[PaperAccountSnapshotPositionInput] = []
        for row in position_rows:
            record = _hydrate_position_record(row)
            apps = self._hydrate_ordered_applications(connection, record.paper_position_id)
            positions_input.append(
                PaperAccountSnapshotPositionInput(
                    record.paper_position_id, record.pair, record.position_side, apps
                )
            )

        ledger_rows = connection.execute(
            "SELECT * FROM live_paper_ledger_entries "
            "WHERE paper_account_id = ? ORDER BY ledger_entry_seq ASC",
            (account_id,),
        ).fetchall()
        ledger_entries = tuple(_hydrate_ledger_entry(row) for row in ledger_rows)
        highest_ledger_entry_seq = max(
            (int(row["ledger_entry_seq"]) for row in ledger_rows), default=0
        )
        highest_application_seq = max(
            (self._max_application_seq(connection, p.paper_position_id) for p in positions_input),
            default=0,
        )

        order_rows = connection.execute(
            "SELECT paper_order_id FROM live_paper_orders WHERE paper_account_id = ?", (account_id,)
        ).fetchall()
        order_events_by_order: dict[str, tuple[PaperOrderEvent, ...]] = {}
        highest_order_event_seq = 0
        for order_row in order_rows:
            events, max_seq = self._hydrate_order_events_with_seq(
                connection, order_row["paper_order_id"]
            )
            order_events_by_order[order_row["paper_order_id"]] = events
            highest_order_event_seq = max(highest_order_event_seq, max_seq)

        observations_by_pair = {obs.pair.symbol: obs for obs in mark_set.observations}
        open_quantities: dict[str, Decimal] = {}
        average_entry_prices: dict[str, Decimal] = {}
        for p in positions_input:
            open_quantities[p.paper_position_id] = project_paper_position_open_quantity(
                p.applications
            )
            entry_apps = tuple(
                a
                for a in p.applications
                if a.application_kind is PaperPositionApplicationKind.ENTRY
            )
            if entry_apps:
                average_entry_prices[p.paper_position_id] = paper_weighted_average_entry_price_v1(
                    entry_apps
                )

        with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
            realized_pnl_total_acc = Decimal(0)
            accrued_swap_total_acc = Decimal(0)
            for entry in ledger_entries:
                if entry.entry_kind is PaperLedgerEntryKind.REALIZED_PNL:
                    realized_pnl_total_acc = realized_pnl_total_acc + entry.amount
                else:
                    accrued_swap_total_acc = accrued_swap_total_acc + entry.amount

        open_position_marks: list[tuple[PaperPositionSide, Decimal, PaperMarketObservation]] = []
        for p in positions_input:
            open_quantity_p = open_quantities[p.paper_position_id]
            if open_quantity_p <= 0:
                continue
            open_position_marks.append(
                (p.position_side, open_quantity_p, observations_by_pair[p.pair.symbol])
            )

        with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
            unrealized_pnl_total = Decimal(0)
            for p in positions_input:
                open_quantity_p = open_quantities[p.paper_position_id]
                if open_quantity_p <= 0:
                    continue
                unrealized_pnl_total = unrealized_pnl_total + paper_unrealized_pnl_v1(
                    position_side=p.position_side,
                    average_entry_price=average_entry_prices[p.paper_position_id],
                    observation=observations_by_pair[p.pair.symbol],
                    open_quantity=open_quantity_p,
                )

        gross_exposure = paper_gross_exposure_v1(open_position_marks)
        equity = paper_account_equity_v1(
            cash=bootstrap.initial_cash,
            realized_pnl_total=realized_pnl_total_acc,
            accrued_swap_total=accrued_swap_total_acc,
            unrealized_pnl_total=unrealized_pnl_total,
        )
        used_margin = paper_used_margin_v1(
            gross_exposure=gross_exposure, leverage=bootstrap.leverage
        )
        available_margin = paper_available_margin_v1(equity=equity, used_margin=used_margin)
        open_position_count = paper_open_position_count_v1(open_quantities)
        open_order_count = paper_open_order_count_v1(order_events_by_order)
        mark_observation_ids = tuple(
            observation.market_observation_id
            for observation in sorted(mark_set.observations, key=lambda o: o.pair.symbol)
        )

        account_snapshot = PaperAccountSnapshot.create(
            paper_account_id=account_id,
            cash=bootstrap.initial_cash,
            realized_pnl_total=realized_pnl_total_acc,
            unrealized_pnl_total=unrealized_pnl_total,
            accrued_swap_total=accrued_swap_total_acc,
            equity=equity,
            used_margin=used_margin,
            available_margin=available_margin,
            gross_exposure=gross_exposure,
            open_position_count=open_position_count,
            open_order_count=open_order_count,
            mark_observation_ids=mark_observation_ids,
            highest_application_seq=highest_application_seq,
            highest_ledger_entry_seq=highest_ledger_entry_seq,
            highest_order_event_seq=highest_order_event_seq,
            margin_policy_version=bootstrap.margin_policy_version,
            unrealized_mark_policy_version=bootstrap.unrealized_mark_policy_version,
            created_at=evaluated_at,
        )
        _insert_or_compare(
            connection,
            "live_paper_account_snapshots",
            *_account_snapshot_row(account_snapshot),
            ("paper_account_snapshot_id",),
            "account snapshot already persisted with different content",
        )
        return position_snapshot, account_snapshot

    # -- Hydration / authentication helpers ----------------------------------

    def _hydrate_position(
        self, connection: sqlite3.Connection, paper_position_id: str
    ) -> PaperPositionRecord | None:
        row = connection.execute(
            "SELECT * FROM live_paper_positions WHERE paper_position_id = ?", (paper_position_id,)
        ).fetchone()
        return None if row is None else _hydrate_position_record(row)

    def _hydrate_account_bootstrap(
        self, connection: sqlite3.Connection, paper_account_id: str
    ) -> PaperAccountBootstrap | None:
        row = connection.execute(
            "SELECT * FROM live_paper_account_bootstraps WHERE paper_account_id = ?",
            (paper_account_id,),
        ).fetchone()
        return None if row is None else _hydrate_account_bootstrap_row(row)

    def _hydrate_fill_policy(
        self, connection: sqlite3.Connection, paper_fill_policy_id: str
    ) -> PaperFillPolicy | None:
        row = connection.execute(
            "SELECT * FROM live_paper_fill_policies WHERE paper_fill_policy_id = ?",
            (paper_fill_policy_id,),
        ).fetchone()
        return None if row is None else _hydrate_fill_policy_row(row)

    def _authenticate_plan(
        self, connection: sqlite3.Connection, plan: FillEvaluationPlan
    ) -> PaperOrder:
        row = connection.execute(
            "SELECT * FROM live_paper_fill_evaluation_plans WHERE fill_evaluation_plan_id = ?",
            (plan.fill_evaluation_plan_id,),
        ).fetchone()
        if row is None:
            raise PaperPersistenceIntegrityError("plan is not persisted")
        order_row = connection.execute(
            "SELECT * FROM live_paper_orders WHERE paper_order_id = ?", (row["paper_order_id"],)
        ).fetchone()
        if order_row is None:
            raise PaperPersistenceIntegrityError("plan references an order that is not persisted")
        order = _hydrate_order_row(order_row)
        persisted_plan = _hydrate_plan_row(row, order.intent_lineage)
        if persisted_plan != plan:
            raise PaperPersistenceConflict("supplied plan does not match its persisted content")
        return order

    def _authenticate_step(
        self, connection: sqlite3.Connection, step: FillEvaluationStep, plan: FillEvaluationPlan
    ) -> None:
        row = connection.execute(
            "SELECT * FROM live_paper_fill_evaluation_steps WHERE fill_evaluation_step_id = ?",
            (step.fill_evaluation_step_id,),
        ).fetchone()
        if row is None:
            raise PaperPersistenceIntegrityError("Step is not persisted")
        persisted_step = _hydrate_step(row)
        if persisted_step != step or step.fill_evaluation_plan_id != plan.fill_evaluation_plan_id:
            raise PaperPersistenceConflict("supplied Step does not match its persisted content")

    def _authenticate_mark_observations(
        self, connection: sqlite3.Connection, mark_observations: tuple[PaperMarketObservation, ...]
    ) -> None:
        for observation in mark_observations:
            if type(observation) is not PaperMarketObservation:
                raise TypeError("mark_observations entries must be exact PaperMarketObservation")
            row = connection.execute(
                "SELECT * FROM live_paper_market_observations WHERE market_observation_id = ?",
                (observation.market_observation_id,),
            ).fetchone()
            if row is None or _hydrate_market_observation(row) != observation:
                raise PaperPersistenceIntegrityError(
                    "mark observation is not an exact persisted market observation"
                )

    def _open_pairs_for_account(
        self, connection: sqlite3.Connection, paper_account_id: str
    ) -> frozenset[CurrencyPair]:
        rows = connection.execute(
            "SELECT * FROM live_paper_positions WHERE paper_account_id = ?", (paper_account_id,)
        ).fetchall()
        open_pairs: set[CurrencyPair] = set()
        for row in rows:
            record = _hydrate_position_record(row)
            applications = self._hydrate_ordered_applications(connection, record.paper_position_id)
            if project_paper_position_open_quantity(applications) > 0:
                open_pairs.add(record.pair)
        return frozenset(open_pairs)

    def _hydrate_ordered_applications(
        self, connection: sqlite3.Connection, paper_position_id: str
    ) -> tuple[PaperPositionFillApplication, ...]:
        rows = connection.execute(
            "SELECT * FROM live_paper_position_fill_applications "
            "WHERE paper_position_id = ? ORDER BY application_seq ASC",
            (paper_position_id,),
        ).fetchall()
        return tuple(_hydrate_application(row) for row in rows)

    def _max_application_seq(self, connection: sqlite3.Connection, paper_position_id: str) -> int:
        return _scalar_int(
            connection,
            "SELECT COALESCE(MAX(application_seq), 0) FROM live_paper_position_fill_applications "
            "WHERE paper_position_id = ?",
            (paper_position_id,),
        )

    def _hydrate_swap_ledger_entries(
        self, connection: sqlite3.Connection, paper_position_id: str
    ) -> tuple[PaperLedgerEntry, ...]:
        rows = connection.execute(
            "SELECT * FROM live_paper_ledger_entries WHERE paper_position_id = ? "
            "AND entry_kind IN ('SWAP_ACCRUAL', 'SWAP_ACCRUAL_CORRECTION') "
            "ORDER BY ledger_entry_seq ASC",
            (paper_position_id,),
        ).fetchall()
        return tuple(_hydrate_ledger_entry(row) for row in rows)

    def _max_ledger_entry_seq_for_position(
        self, connection: sqlite3.Connection, paper_position_id: str
    ) -> int:
        return _scalar_int(
            connection,
            "SELECT COALESCE(MAX(ledger_entry_seq), 0) FROM live_paper_ledger_entries "
            "WHERE paper_position_id = ?",
            (paper_position_id,),
        )

    def _hydrate_order_events_with_seq(
        self, connection: sqlite3.Connection, paper_order_id: str
    ) -> tuple[tuple[PaperOrderEvent, ...], int]:
        rows = connection.execute(
            "SELECT * FROM live_paper_order_events "
            "WHERE paper_order_id = ? ORDER BY event_ordinal ASC",
            (paper_order_id,),
        ).fetchall()
        events = tuple(_hydrate_order_event(row) for row in rows)
        max_seq = max((int(row["order_event_seq"]) for row in rows), default=0)
        return events, max_seq

    def _hydrate_ordered_fills_for_plan(
        self, connection: sqlite3.Connection, plan_id: str
    ) -> tuple[PaperFill, ...]:
        rows = connection.execute(
            "SELECT f.* FROM live_paper_fills f "
            "JOIN live_paper_fill_evaluation_steps s "
            "ON s.fill_evaluation_step_id = f.fill_evaluation_step_id "
            "WHERE s.fill_evaluation_plan_id = ? ORDER BY s.ordinal ASC",
            (plan_id,),
        ).fetchall()
        return tuple(_hydrate_fill(row) for row in rows)

    def _hydrate_fill_for_step(
        self, connection: sqlite3.Connection, step_id: str
    ) -> PaperFill | None:
        row = connection.execute(
            "SELECT * FROM live_paper_fills WHERE fill_evaluation_step_id = ?", (step_id,)
        ).fetchone()
        return None if row is None else _hydrate_fill(row)

    def _reservation_release_exists(
        self, connection: sqlite3.Connection, paper_order_id: str
    ) -> bool:
        row = connection.execute(
            "SELECT 1 FROM live_paper_reservation_releases WHERE paper_order_id = ?",
            (paper_order_id,),
        ).fetchone()
        return row is not None

    def _reservation_consumed_total(
        self, connection: sqlite3.Connection, idempotency_key: str
    ) -> Decimal:
        rows = connection.execute(
            "SELECT consumed_quantity FROM live_paper_reservation_consumptions "
            "WHERE close_intent_idempotency_key = ?",
            (idempotency_key,),
        ).fetchall()
        with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
            total = Decimal(0)
            for row in rows:
                total = total + Decimal(row["consumed_quantity"])
        return total

    def _m2d_intent_quantity(self, connection: sqlite3.Connection, idempotency_key: str) -> Decimal:
        row = connection.execute(
            "SELECT quantity FROM live_ordinary_close_approved_intents WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            raise PaperPersistenceIntegrityError(
                "no persisted M2-D approved close Intent for this idempotency key"
            )
        return Decimal(row["quantity"])

    def _authenticate_ordinary_close_intent(
        self, connection: sqlite3.Connection, intent: ApprovedCloseIntent
    ) -> None:
        row = connection.execute(
            "SELECT * FROM live_ordinary_close_approved_intents WHERE idempotency_key = ?",
            (intent.idempotency_key,),
        ).fetchone()
        if row is None:
            raise PaperPersistenceIntegrityError(
                "no persisted M2-D approved close Intent for this idempotency key"
            )
        persisted = ApprovedCloseIntent(
            row["close_candidate_id"],
            row["portfolio_decision_id"],
            row["risk_decision_id"],
            row["capacity_evidence_id"],
            PositionId(row["position_id"]),
            CurrencyPair.parse(row["pair"]),
            Side(row["side"]),
            Decimal(row["quantity"]),
            ExecutionAuthorityMode(row["authority"]),
            row["idempotency_key"],
            datetime.fromisoformat(row["created_at"]),
        )
        if persisted != intent:
            raise PaperPersistenceIntegrityError(
                "supplied ApprovedCloseIntent does not match its persisted M2-D content"
            )

    def _max_plan_instant(
        self, connection: sqlite3.Connection, plan_id: str, paper_order_id: str
    ) -> datetime | None:
        queries: tuple[tuple[str, tuple[object, ...]], ...] = (
            (
                "SELECT MAX(appended_at) FROM live_paper_order_events WHERE paper_order_id = ?",
                (paper_order_id,),
            ),
            (
                "SELECT MAX(created_at) FROM live_paper_fill_evaluation_steps "
                "WHERE fill_evaluation_plan_id = ?",
                (plan_id,),
            ),
            (
                "SELECT MAX(a.evaluated_at) FROM live_paper_fill_evaluation_attempts a "
                "JOIN live_paper_fill_evaluation_steps s "
                "ON s.fill_evaluation_step_id = a.fill_evaluation_step_id "
                "WHERE s.fill_evaluation_plan_id = ?",
                (plan_id,),
            ),
            (
                "SELECT MAX(c.resolved_at) FROM live_paper_step_terminal_claims c "
                "JOIN live_paper_fill_evaluation_steps s "
                "ON s.fill_evaluation_step_id = c.fill_evaluation_step_id "
                "WHERE s.fill_evaluation_plan_id = ?",
                (plan_id,),
            ),
            (
                "SELECT MAX(selected_at) FROM live_paper_market_observation_selections "
                "WHERE fill_evaluation_plan_id = ?",
                (plan_id,),
            ),
            (
                "SELECT MAX(n.resolved_at) FROM live_paper_no_market_outcomes n "
                "JOIN live_paper_fill_evaluation_steps s "
                "ON s.fill_evaluation_step_id = n.fill_evaluation_step_id "
                "WHERE s.fill_evaluation_plan_id = ?",
                (plan_id,),
            ),
            (
                "SELECT MAX(f.created_at) FROM live_paper_fills f "
                "JOIN live_paper_fill_evaluation_steps s "
                "ON s.fill_evaluation_step_id = f.fill_evaluation_step_id "
                "WHERE s.fill_evaluation_plan_id = ?",
                (plan_id,),
            ),
        )
        parsed = [
            datetime.fromisoformat(value)
            for value in (_scalar(connection, sql, params) for sql, params in queries)
            if isinstance(value, str)
        ]
        return max(parsed) if parsed else None

    def _max_scan_set_instant(
        self, connection: sqlite3.Connection, paper_account_id: str
    ) -> datetime | None:
        queries: tuple[tuple[str, tuple[object, ...]], ...] = (
            (
                "SELECT MAX(oe.appended_at) FROM live_paper_order_events oe "
                "JOIN live_paper_orders o ON o.paper_order_id = oe.paper_order_id "
                "WHERE o.paper_account_id = ?",
                (paper_account_id,),
            ),
            (
                "SELECT MAX(s.created_at) FROM live_paper_fill_evaluation_steps s "
                "JOIN live_paper_fill_evaluation_plans p "
                "ON p.fill_evaluation_plan_id = s.fill_evaluation_plan_id "
                "JOIN live_paper_orders o ON o.paper_order_id = p.paper_order_id "
                "WHERE o.paper_account_id = ?",
                (paper_account_id,),
            ),
            (
                "SELECT MAX(a.evaluated_at) FROM live_paper_fill_evaluation_attempts a "
                "JOIN live_paper_fill_evaluation_steps s "
                "ON s.fill_evaluation_step_id = a.fill_evaluation_step_id "
                "JOIN live_paper_fill_evaluation_plans p "
                "ON p.fill_evaluation_plan_id = s.fill_evaluation_plan_id "
                "JOIN live_paper_orders o ON o.paper_order_id = p.paper_order_id "
                "WHERE o.paper_account_id = ?",
                (paper_account_id,),
            ),
            (
                "SELECT MAX(sa.created_at) FROM live_paper_swap_accruals sa "
                "JOIN live_paper_positions pos ON pos.paper_position_id = sa.paper_position_id "
                "WHERE pos.paper_account_id = ?",
                (paper_account_id,),
            ),
            (
                "SELECT MAX(sn.created_at) FROM live_paper_swap_non_accruals sn "
                "JOIN live_paper_positions pos ON pos.paper_position_id = sn.paper_position_id "
                "WHERE pos.paper_account_id = ?",
                (paper_account_id,),
            ),
            (
                "SELECT MAX(c.created_at) FROM live_paper_swap_accrual_corrections c "
                "JOIN live_paper_swap_accruals sa "
                "ON sa.paper_swap_accrual_id = c.corrected_accrual_id "
                "JOIN live_paper_positions pos ON pos.paper_position_id = sa.paper_position_id "
                "WHERE pos.paper_account_id = ?",
                (paper_account_id,),
            ),
            (
                "SELECT MAX(created_at) FROM live_paper_reconciliation_results "
                "WHERE paper_account_id = ?",
                (paper_account_id,),
            ),
        )
        parsed = [
            datetime.fromisoformat(value)
            for value in (_scalar(connection, sql, params) for sql, params in queries)
            if isinstance(value, str)
        ]
        return max(parsed) if parsed else None
