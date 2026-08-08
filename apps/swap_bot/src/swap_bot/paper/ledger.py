from __future__ import annotations

import decimal
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from fx_core import Currency, CurrencyPair
from fx_core.time import require_utc

from ..adoption import digest
from ..models import Side
from ..strategy.swap_evidence import OperationalSwapEvidence
from ..swap import SwapAvailability
from .contracts import (
    PAPER_EXACT_ARITHMETIC_V1,
    PAPER_QUOTIENT_ARITHMETIC_V1,
    PaperFill,
    PaperIntentKind,
    PaperMarketObservation,
    PaperOrder,
    PaperOrderEvent,
    PaperOrderState,
    project_paper_order_state,
)

PAPER_ACCOUNT_BOOTSTRAP_CONTRACT_VERSION = "paper-account-bootstrap-v1"
PAPER_POSITION_FILL_APPLICATION_CONTRACT_VERSION = "paper-position-fill-application-v1"
PAPER_LEDGER_ENTRY_CONTRACT_VERSION = "paper-ledger-entry-v1"
PAPER_POSITION_SNAPSHOT_CONTRACT_VERSION = "paper-position-snapshot-v1"
PAPER_ACCOUNT_SNAPSHOT_CONTRACT_VERSION = "paper-account-snapshot-v1"
PAPER_SWAP_ACCRUAL_POLICY_CONTRACT_VERSION = "paper-swap-accrual-policy-v1"
PAPER_SWAP_ACCRUAL_RECORD_CONTRACT_VERSION = "paper-swap-accrual-record-v1"
PAPER_SWAP_NON_ACCRUAL_CONTRACT_VERSION = "paper-swap-non-accrual-v1"
PAPER_SWAP_ACCRUAL_CORRECTION_CONTRACT_VERSION = "paper-swap-accrual-correction-v1"
PAPER_RECONCILIATION_RESULT_CONTRACT_VERSION = "paper-reconciliation-result-v1"

# Frozen named formula/contract version strings (see spec.md "### Formulas" and
# "### Swap accrual"). Every M3 numeric formula names exactly one of these.
PAPER_WEIGHTED_AVERAGE_ENTRY_PRICE_V1 = "paper-weighted-average-entry-price-v1"
PAPER_REALIZED_PNL_V1 = "paper-realized-pnl-v1"
PAPER_UNREALIZED_PNL_V1 = "paper-unrealized-pnl-v1"
PAPER_GROSS_EXPOSURE_V1 = "paper-gross-exposure-v1"
PAPER_ACCOUNT_EQUITY_V1 = "paper-account-equity-v1"
PAPER_USED_MARGIN_V1 = "paper-used-margin-v1"
PAPER_AVAILABLE_MARGIN_V1 = "paper-available-margin-v1"
PAPER_OPEN_POSITION_COUNT_V1 = "paper-open-position-count-v1"
PAPER_OPEN_ORDER_COUNT_V1 = "paper-open-order-count-v1"
PAPER_ACCOUNT_MARK_SET_V1 = "paper-account-mark-set-v1"
PAPER_SWAP_ROLLOVER_INSTANT_V1 = "paper-swap-rollover-instant-v1"
PAPER_SWAP_ACCRUAL_V1 = "paper-swap-accrual-v1"

_JPY = Currency("JPY")


class PaperLedgerIntegrityError(ValueError):
    """A frozen ledger invariant was violated (see spec.md "Position projection")."""


# ---------------------------------------------------------------------------
# Small intrinsic validators (established M2-D/M3-B1 idiom: exact-type,
# non-blank, UTC-aware; never isinstance, never a bare truthiness check).
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


def _finite_decimal(value: object, label: str) -> None:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError(f"{label} must be a finite Decimal")


def _positive_timedelta(value: object, label: str) -> None:
    if type(value) is not timedelta or value <= timedelta(0):
        raise ValueError(f"{label} must be a positive exact timedelta")


def _nonnegative_int(value: object, label: str) -> None:
    if type(value) is not int or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be an exact int >= 0")


def _exact_pair(value: object, label: str = "pair") -> None:
    if type(value) is not CurrencyPair:
        raise TypeError(f"{label} must be exact CurrencyPair")
    CurrencyPair.__post_init__(value)


def _exact_jpy_currency(value: object, label: str) -> None:
    if type(value) is not Currency:
        raise TypeError(f"{label} must be exact Currency")
    if value != _JPY:
        raise ValueError(f"{label} must be JPY")


def _exact_position_side(value: object, label: str = "position_side") -> None:
    if type(value) is not PaperPositionSide:
        raise TypeError(f"{label} must be exact PaperPositionSide")


def _exact_application_kind(value: object, label: str = "application_kind") -> None:
    if type(value) is not PaperPositionApplicationKind:
        raise TypeError(f"{label} must be exact PaperPositionApplicationKind")


def _ordered_str_tuple(value: object, label: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{label} must be exact tuple")
    for item in value:
        _text(item, f"{label} entry")
    return value


def _ascending_unique(values: Sequence[str], label: str) -> None:
    ordered = list(values)
    if ordered != sorted(ordered):
        raise ValueError(f"{label} must be ascending")
    if len(ordered) != len(set(ordered)):
        raise ValueError(f"{label} must not contain duplicates")


# ---------------------------------------------------------------------------
# Position and ledger enums
# ---------------------------------------------------------------------------


class PaperPositionSide(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class PaperPositionApplicationKind(StrEnum):
    ENTRY = "ENTRY"
    REDUCE_ONLY = "REDUCE_ONLY"


class PaperLedgerEntryKind(StrEnum):
    REALIZED_PNL = "REALIZED_PNL"
    SWAP_ACCRUAL = "SWAP_ACCRUAL"
    SWAP_ACCRUAL_CORRECTION = "SWAP_ACCRUAL_CORRECTION"


# ---------------------------------------------------------------------------
# PaperAccountBootstrap
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PaperAccountBootstrap:
    paper_account_id: str
    bootstrap_contract_version: str
    initial_cash: Decimal
    settlement_currency: Currency
    margin_policy_version: str
    leverage: Decimal
    unrealized_mark_policy_version: str

    @classmethod
    def create(
        cls,
        *,
        initial_cash: Decimal,
        settlement_currency: Currency,
        margin_policy_version: str,
        leverage: Decimal,
        unrealized_mark_policy_version: str,
    ) -> PaperAccountBootstrap:
        payload = _bootstrap_payload(
            initial_cash=initial_cash,
            settlement_currency=settlement_currency,
            margin_policy_version=margin_policy_version,
            leverage=leverage,
            unrealized_mark_policy_version=unrealized_mark_policy_version,
        )
        return cls(
            "paper-account-" + digest(payload),
            PAPER_ACCOUNT_BOOTSTRAP_CONTRACT_VERSION,
            initial_cash,
            settlement_currency,
            margin_policy_version,
            leverage,
            unrealized_mark_policy_version,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _bootstrap_payload(
            initial_cash=self.initial_cash,
            settlement_currency=self.settlement_currency,
            margin_policy_version=self.margin_policy_version,
            leverage=self.leverage,
            unrealized_mark_policy_version=self.unrealized_mark_policy_version,
        )

    def __post_init__(self) -> None:
        if type(self.paper_account_id) is not str:
            raise TypeError("paper_account_id must be exact str")
        if (
            type(self.bootstrap_contract_version) is not str
            or self.bootstrap_contract_version != PAPER_ACCOUNT_BOOTSTRAP_CONTRACT_VERSION
        ):
            raise ValueError("unsupported bootstrap contract")
        _positive_finite_decimal(self.initial_cash, "initial_cash")
        _exact_jpy_currency(self.settlement_currency, "settlement_currency")
        _text(self.margin_policy_version, "margin_policy_version")
        _positive_finite_decimal(self.leverage, "leverage")
        _text(self.unrealized_mark_policy_version, "unrealized_mark_policy_version")
        expected_id = "paper-account-" + digest(self.identity_payload)
        if self.paper_account_id != expected_id:
            raise ValueError("paper_account_id does not match content")


def _bootstrap_payload(
    *,
    initial_cash: Decimal,
    settlement_currency: Currency,
    margin_policy_version: str,
    leverage: Decimal,
    unrealized_mark_policy_version: str,
) -> dict[str, object]:
    return {
        "bootstrap_contract_version": PAPER_ACCOUNT_BOOTSTRAP_CONTRACT_VERSION,
        "initial_cash": str(initial_cash),
        "settlement_currency": settlement_currency.code,
        "margin_policy_version": margin_policy_version,
        "leverage": str(leverage),
        "unrealized_mark_policy_version": unrealized_mark_policy_version,
    }


# ---------------------------------------------------------------------------
# PaperPositionFillApplication and position projection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PaperPositionFillApplication:
    paper_position_fill_application_id: str
    application_contract_version: str
    paper_position_id: str
    paper_order_id: str
    paper_fill_id: str
    application_kind: PaperPositionApplicationKind
    quantity: Decimal
    price: Decimal
    open_quantity_after: Decimal
    realized_pnl_amount: Decimal | None
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        paper_position_id: str,
        paper_order_id: str,
        paper_fill_id: str,
        application_kind: PaperPositionApplicationKind,
        quantity: Decimal,
        price: Decimal,
        open_quantity_after: Decimal,
        realized_pnl_amount: Decimal | None,
        created_at: datetime,
    ) -> PaperPositionFillApplication:
        payload = _position_fill_application_payload(
            paper_position_id=paper_position_id,
            paper_order_id=paper_order_id,
            paper_fill_id=paper_fill_id,
            application_kind=application_kind,
            quantity=quantity,
            price=price,
            open_quantity_after=open_quantity_after,
            realized_pnl_amount=realized_pnl_amount,
        )
        return cls(
            "paper-position-application-" + digest(payload),
            PAPER_POSITION_FILL_APPLICATION_CONTRACT_VERSION,
            paper_position_id,
            paper_order_id,
            paper_fill_id,
            application_kind,
            quantity,
            price,
            open_quantity_after,
            realized_pnl_amount,
            created_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _position_fill_application_payload(
            paper_position_id=self.paper_position_id,
            paper_order_id=self.paper_order_id,
            paper_fill_id=self.paper_fill_id,
            application_kind=self.application_kind,
            quantity=self.quantity,
            price=self.price,
            open_quantity_after=self.open_quantity_after,
            realized_pnl_amount=self.realized_pnl_amount,
        )

    def __post_init__(self) -> None:
        if type(self.paper_position_fill_application_id) is not str:
            raise TypeError("paper_position_fill_application_id must be exact str")
        if (
            type(self.application_contract_version) is not str
            or self.application_contract_version != PAPER_POSITION_FILL_APPLICATION_CONTRACT_VERSION
        ):
            raise ValueError("unsupported position fill application contract")
        for value, label in (
            (self.paper_position_id, "paper_position_id"),
            (self.paper_order_id, "paper_order_id"),
            (self.paper_fill_id, "paper_fill_id"),
        ):
            _text(value, label)
        _exact_application_kind(self.application_kind)
        _positive_finite_decimal(self.quantity, "quantity")
        _positive_finite_decimal(self.price, "price")
        _nonnegative_finite_decimal(self.open_quantity_after, "open_quantity_after")
        if self.application_kind is PaperPositionApplicationKind.ENTRY:
            if self.realized_pnl_amount is not None:
                raise ValueError("ENTRY application must carry realized_pnl_amount None")
        else:
            _finite_decimal(self.realized_pnl_amount, "realized_pnl_amount")
        _utc(self.created_at, "created_at")
        expected_id = "paper-position-application-" + digest(self.identity_payload)
        if self.paper_position_fill_application_id != expected_id:
            raise ValueError("paper_position_fill_application_id does not match content")


def _position_fill_application_payload(
    *,
    paper_position_id: str,
    paper_order_id: str,
    paper_fill_id: str,
    application_kind: PaperPositionApplicationKind,
    quantity: Decimal,
    price: Decimal,
    open_quantity_after: Decimal,
    realized_pnl_amount: Decimal | None,
) -> dict[str, object]:
    return {
        "application_contract_version": PAPER_POSITION_FILL_APPLICATION_CONTRACT_VERSION,
        "paper_position_id": paper_position_id,
        "paper_order_id": paper_order_id,
        "paper_fill_id": paper_fill_id,
        "application_kind": application_kind.value,
        "quantity": str(quantity),
        "price": str(price),
        "open_quantity_after": str(open_quantity_after),
        "realized_pnl_amount": None if realized_pnl_amount is None else str(realized_pnl_amount),
    }


def project_paper_position_open_quantity(
    applications: Sequence[PaperPositionFillApplication],
) -> Decimal:
    ordered = tuple(applications)
    for application in ordered:
        if type(application) is not PaperPositionFillApplication:
            raise TypeError("applications entries must be exact PaperPositionFillApplication")
    seen_reduce_only = False
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        open_quantity = Decimal(0)
        for application in ordered:
            if application.application_kind is PaperPositionApplicationKind.ENTRY:
                if seen_reduce_only:
                    raise PaperLedgerIntegrityError(
                        "an ENTRY application on a position that already holds a REDUCE_ONLY "
                        "application is a ledger integrity error"
                    )
                open_quantity = open_quantity + application.quantity
            else:
                seen_reduce_only = True
                if application.quantity > open_quantity:
                    raise PaperLedgerIntegrityError(
                        "a REDUCE_ONLY application with quantity greater than open_quantity is "
                        "a ledger integrity error"
                    )
                open_quantity = open_quantity - application.quantity
        if open_quantity < 0:
            raise PaperLedgerIntegrityError("open_quantity must never be negative")
    return open_quantity


def _open_quantity_before(prior: tuple[PaperPositionFillApplication, ...]) -> Decimal:
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        total = Decimal(0)
        for application in prior:
            if application.application_kind is PaperPositionApplicationKind.ENTRY:
                total = total + application.quantity
            else:
                total = total - application.quantity
    return total


def compute_position_application_fields(
    prior_applications: Sequence[PaperPositionFillApplication],
    *,
    position_side: PaperPositionSide,
    application_kind: PaperPositionApplicationKind,
    quantity: Decimal,
    price: Decimal,
) -> tuple[Decimal, Decimal | None]:
    _exact_position_side(position_side)
    _exact_application_kind(application_kind)
    _positive_finite_decimal(quantity, "quantity")
    _positive_finite_decimal(price, "price")
    prior = tuple(prior_applications)
    for application in prior:
        if type(application) is not PaperPositionFillApplication:
            raise TypeError("prior_applications entries must be exact PaperPositionFillApplication")
    has_reduce_only = any(
        application.application_kind is PaperPositionApplicationKind.REDUCE_ONLY
        for application in prior
    )
    open_quantity_before = _open_quantity_before(prior)
    if application_kind is PaperPositionApplicationKind.ENTRY:
        if has_reduce_only:
            raise PaperLedgerIntegrityError(
                "an ENTRY application on a position that already holds a REDUCE_ONLY "
                "application is a ledger integrity error"
            )
        with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
            open_quantity_after = open_quantity_before + quantity
        return open_quantity_after, None
    if quantity > open_quantity_before:
        raise PaperLedgerIntegrityError(
            "a REDUCE_ONLY application with quantity greater than open_quantity is a ledger "
            "integrity error"
        )
    entry_applications = tuple(
        application
        for application in prior
        if application.application_kind is PaperPositionApplicationKind.ENTRY
    )
    average_entry_price = paper_weighted_average_entry_price_v1(entry_applications)
    realized_pnl_amount = paper_realized_pnl_v1(
        position_side=position_side,
        average_entry_price=average_entry_price,
        close_price=price,
        quantity=quantity,
    )
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        open_quantity_after = open_quantity_before - quantity
    return open_quantity_after, realized_pnl_amount


# ---------------------------------------------------------------------------
# Ledger entries
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PaperLedgerEntry:
    ledger_entry_id: str
    entry_contract_version: str
    paper_account_id: str
    paper_position_id: str
    entry_kind: PaperLedgerEntryKind
    settlement_currency: Currency
    amount: Decimal
    source_evidence_kind: str
    source_evidence_id: str
    formula_version: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        paper_account_id: str,
        paper_position_id: str,
        entry_kind: PaperLedgerEntryKind,
        settlement_currency: Currency,
        amount: Decimal,
        source_evidence_kind: str,
        source_evidence_id: str,
        formula_version: str,
        created_at: datetime,
    ) -> PaperLedgerEntry:
        payload = _ledger_entry_payload(
            paper_account_id=paper_account_id,
            paper_position_id=paper_position_id,
            entry_kind=entry_kind,
            settlement_currency=settlement_currency,
            amount=amount,
            source_evidence_kind=source_evidence_kind,
            source_evidence_id=source_evidence_id,
            formula_version=formula_version,
        )
        return cls(
            "paper-ledger-entry-" + digest(payload),
            PAPER_LEDGER_ENTRY_CONTRACT_VERSION,
            paper_account_id,
            paper_position_id,
            entry_kind,
            settlement_currency,
            amount,
            source_evidence_kind,
            source_evidence_id,
            formula_version,
            created_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _ledger_entry_payload(
            paper_account_id=self.paper_account_id,
            paper_position_id=self.paper_position_id,
            entry_kind=self.entry_kind,
            settlement_currency=self.settlement_currency,
            amount=self.amount,
            source_evidence_kind=self.source_evidence_kind,
            source_evidence_id=self.source_evidence_id,
            formula_version=self.formula_version,
        )

    def __post_init__(self) -> None:
        if type(self.ledger_entry_id) is not str:
            raise TypeError("ledger_entry_id must be exact str")
        if (
            type(self.entry_contract_version) is not str
            or self.entry_contract_version != PAPER_LEDGER_ENTRY_CONTRACT_VERSION
        ):
            raise ValueError("unsupported ledger entry contract")
        _text(self.paper_account_id, "paper_account_id")
        _text(self.paper_position_id, "paper_position_id")
        if type(self.entry_kind) is not PaperLedgerEntryKind:
            raise TypeError("entry_kind must be exact PaperLedgerEntryKind")
        _exact_jpy_currency(self.settlement_currency, "settlement_currency")
        _finite_decimal(self.amount, "amount")
        _text(self.source_evidence_kind, "source_evidence_kind")
        _text(self.source_evidence_id, "source_evidence_id")
        _text(self.formula_version, "formula_version")
        _utc(self.created_at, "created_at")
        expected_id = "paper-ledger-entry-" + digest(self.identity_payload)
        if self.ledger_entry_id != expected_id:
            raise ValueError("ledger_entry_id does not match content")


def _ledger_entry_payload(
    *,
    paper_account_id: str,
    paper_position_id: str,
    entry_kind: PaperLedgerEntryKind,
    settlement_currency: Currency,
    amount: Decimal,
    source_evidence_kind: str,
    source_evidence_id: str,
    formula_version: str,
) -> dict[str, object]:
    return {
        "entry_contract_version": PAPER_LEDGER_ENTRY_CONTRACT_VERSION,
        "paper_account_id": paper_account_id,
        "paper_position_id": paper_position_id,
        "entry_kind": entry_kind.value,
        "settlement_currency": settlement_currency.code,
        "amount": str(amount),
        "source_evidence_kind": source_evidence_kind,
        "source_evidence_id": source_evidence_id,
        "formula_version": formula_version,
    }


# ---------------------------------------------------------------------------
# PaperAccountMarkSet and its coverage rule
# ---------------------------------------------------------------------------


def paper_account_mark_set_required_coverage_v1(
    *,
    pre_transaction_open_pairs: frozenset[CurrencyPair],
    order_pair: CurrencyPair | None,
) -> frozenset[CurrencyPair]:
    if type(pre_transaction_open_pairs) is not frozenset:
        raise TypeError("pre_transaction_open_pairs must be exact frozenset")
    for pair in pre_transaction_open_pairs:
        _exact_pair(pair, "pre_transaction_open_pairs entry")
    if order_pair is None:
        return pre_transaction_open_pairs
    _exact_pair(order_pair, "order_pair")
    return pre_transaction_open_pairs | {order_pair}


@dataclass(frozen=True, slots=True)
class PaperAccountMarkSet:
    mark_set_contract_version: str
    observations: tuple[PaperMarketObservation, ...]

    @classmethod
    def create(
        cls,
        observations: Sequence[PaperMarketObservation],
        *,
        coverage_set: frozenset[CurrencyPair],
        bounding_instant: datetime,
    ) -> PaperAccountMarkSet:
        if type(observations) is not tuple:
            raise TypeError("observations must be exact tuple")
        for observation in observations:
            if type(observation) is not PaperMarketObservation:
                raise TypeError("observations entries must be exact PaperMarketObservation")
            PaperMarketObservation.__post_init__(observation)
        if type(coverage_set) is not frozenset:
            raise TypeError("coverage_set must be exact frozenset")
        for pair in coverage_set:
            _exact_pair(pair, "coverage_set entry")
        _utc(bounding_instant, "bounding_instant")
        pairs_seen: list[CurrencyPair] = []
        for observation in observations:
            if observation.received_at > bounding_instant:
                raise ValueError("mark received_at must not be after bounding_instant")
            pairs_seen.append(observation.pair)
        if len(pairs_seen) != len(set(pairs_seen)):
            raise ValueError("mark set must not repeat a Pair")
        if set(pairs_seen) != coverage_set:
            raise ValueError("mark set must cover exactly the required coverage set")
        ordered = tuple(sorted(observations, key=lambda observation: observation.pair.symbol))
        if observations != ordered:
            raise ValueError("mark set observations must be ordered by pair.symbol")
        return cls(PAPER_ACCOUNT_MARK_SET_V1, tuple(observations))

    def __post_init__(self) -> None:
        if (
            type(self.mark_set_contract_version) is not str
            or self.mark_set_contract_version != PAPER_ACCOUNT_MARK_SET_V1
        ):
            raise ValueError("unsupported mark set contract")
        if type(self.observations) is not tuple:
            raise TypeError("observations must be exact tuple")
        for observation in self.observations:
            if type(observation) is not PaperMarketObservation:
                raise TypeError("observations entries must be exact PaperMarketObservation")


# ---------------------------------------------------------------------------
# The seven named account/position formulas
# ---------------------------------------------------------------------------


def paper_weighted_average_entry_price_v1(
    entry_applications: Sequence[PaperPositionFillApplication],
) -> Decimal:
    applications = tuple(entry_applications)
    if not applications:
        raise ValueError("average entry price requires at least one ENTRY application")
    for application in applications:
        if type(application) is not PaperPositionFillApplication:
            raise TypeError("entry_applications entries must be exact PaperPositionFillApplication")
        if application.application_kind is not PaperPositionApplicationKind.ENTRY:
            raise ValueError("average entry price requires only ENTRY applications")
    with decimal.localcontext(PAPER_QUOTIENT_ARITHMETIC_V1):
        total_notional = Decimal(0)
        total_quantity = Decimal(0)
        for application in applications:
            total_notional = total_notional + application.price * application.quantity
            total_quantity = total_quantity + application.quantity
        average = total_notional / total_quantity
    return average


def paper_realized_pnl_v1(
    *,
    position_side: PaperPositionSide,
    average_entry_price: Decimal,
    close_price: Decimal,
    quantity: Decimal,
) -> Decimal:
    _exact_position_side(position_side)
    _positive_finite_decimal(average_entry_price, "average_entry_price")
    _positive_finite_decimal(close_price, "close_price")
    _positive_finite_decimal(quantity, "quantity")
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        if position_side is PaperPositionSide.LONG:
            return (close_price - average_entry_price) * quantity
        return (average_entry_price - close_price) * quantity


def paper_unrealized_pnl_v1(
    *,
    position_side: PaperPositionSide,
    average_entry_price: Decimal,
    observation: PaperMarketObservation,
    open_quantity: Decimal,
) -> Decimal:
    _exact_position_side(position_side)
    _positive_finite_decimal(average_entry_price, "average_entry_price")
    if type(observation) is not PaperMarketObservation:
        raise TypeError("observation must be exact PaperMarketObservation")
    PaperMarketObservation.__post_init__(observation)
    _positive_finite_decimal(open_quantity, "open_quantity")
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        if position_side is PaperPositionSide.LONG:
            return (observation.bid - average_entry_price) * open_quantity
        return (average_entry_price - observation.ask) * open_quantity


def paper_gross_exposure_v1(
    open_positions: Sequence[tuple[PaperPositionSide, Decimal, PaperMarketObservation]],
) -> Decimal:
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        total = Decimal(0)
        for position_side, open_quantity, observation in open_positions:
            _exact_position_side(position_side)
            _positive_finite_decimal(open_quantity, "open_quantity")
            if type(observation) is not PaperMarketObservation:
                raise TypeError("observation must be exact PaperMarketObservation")
            PaperMarketObservation.__post_init__(observation)
            mark_price = (
                observation.bid if position_side is PaperPositionSide.LONG else observation.ask
            )
            total = total + abs(mark_price * open_quantity)
    return total


def paper_account_equity_v1(
    *,
    cash: Decimal,
    realized_pnl_total: Decimal,
    accrued_swap_total: Decimal,
    unrealized_pnl_total: Decimal,
) -> Decimal:
    _positive_finite_decimal(cash, "cash")
    _finite_decimal(realized_pnl_total, "realized_pnl_total")
    _finite_decimal(accrued_swap_total, "accrued_swap_total")
    _finite_decimal(unrealized_pnl_total, "unrealized_pnl_total")
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        return cash + realized_pnl_total + accrued_swap_total + unrealized_pnl_total


def paper_used_margin_v1(*, gross_exposure: Decimal, leverage: Decimal) -> Decimal:
    _nonnegative_finite_decimal(gross_exposure, "gross_exposure")
    _positive_finite_decimal(leverage, "leverage")
    with decimal.localcontext(PAPER_QUOTIENT_ARITHMETIC_V1):
        return gross_exposure / leverage


def paper_available_margin_v1(*, equity: Decimal, used_margin: Decimal) -> Decimal:
    _finite_decimal(equity, "equity")
    _nonnegative_finite_decimal(used_margin, "used_margin")
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        return equity - used_margin


def paper_open_position_count_v1(open_quantities_by_position: Mapping[str, Decimal]) -> int:
    count = 0
    for open_quantity in open_quantities_by_position.values():
        if (
            type(open_quantity) is not Decimal
            or not open_quantity.is_finite()
            or open_quantity < 0
        ):
            raise ValueError("open_quantities_by_position values must be nonnegative finite")
        if open_quantity > 0:
            count += 1
    return count


_OPEN_ORDER_STATES = frozenset(
    {PaperOrderState.ACCEPTED, PaperOrderState.OPEN, PaperOrderState.PARTIALLY_FILLED}
)


def paper_open_order_count_v1(
    order_events_by_order: Mapping[str, Sequence[PaperOrderEvent]],
) -> int:
    count = 0
    for events in order_events_by_order.values():
        ordered_events = tuple(events)
        if not ordered_events:
            # An order with no event at or below the boundary did not yet
            # exist at that boundary (spec.md "Formulas"); it is excluded
            # before project_paper_order_state is ever called.
            continue
        state = project_paper_order_state(ordered_events)
        if state in _OPEN_ORDER_STATES:
            count += 1
    return count


# ---------------------------------------------------------------------------
# PaperPositionSnapshot and PaperAccountSnapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PaperPositionSnapshot:
    paper_position_snapshot_id: str
    snapshot_contract_version: str
    paper_account_id: str
    paper_position_id: str
    pair: CurrencyPair
    position_side: PaperPositionSide
    open_quantity: Decimal
    average_entry_price: Decimal
    realized_pnl_total: Decimal
    accrued_swap_total: Decimal
    highest_application_seq: int
    highest_ledger_entry_seq: int
    average_entry_price_formula_version: str
    realized_pnl_formula_version: str
    swap_accrual_formula_version: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        paper_account_id: str,
        paper_position_id: str,
        pair: CurrencyPair,
        position_side: PaperPositionSide,
        open_quantity: Decimal,
        average_entry_price: Decimal,
        realized_pnl_total: Decimal,
        accrued_swap_total: Decimal,
        highest_application_seq: int,
        highest_ledger_entry_seq: int,
        created_at: datetime,
    ) -> PaperPositionSnapshot:
        payload = _position_snapshot_payload(
            paper_account_id=paper_account_id,
            paper_position_id=paper_position_id,
            pair=pair,
            position_side=position_side,
            open_quantity=open_quantity,
            average_entry_price=average_entry_price,
            realized_pnl_total=realized_pnl_total,
            accrued_swap_total=accrued_swap_total,
            highest_application_seq=highest_application_seq,
            highest_ledger_entry_seq=highest_ledger_entry_seq,
        )
        return cls(
            "paper-position-snapshot-" + digest(payload),
            PAPER_POSITION_SNAPSHOT_CONTRACT_VERSION,
            paper_account_id,
            paper_position_id,
            pair,
            position_side,
            open_quantity,
            average_entry_price,
            realized_pnl_total,
            accrued_swap_total,
            highest_application_seq,
            highest_ledger_entry_seq,
            PAPER_WEIGHTED_AVERAGE_ENTRY_PRICE_V1,
            PAPER_REALIZED_PNL_V1,
            PAPER_SWAP_ACCRUAL_V1,
            created_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _position_snapshot_payload(
            paper_account_id=self.paper_account_id,
            paper_position_id=self.paper_position_id,
            pair=self.pair,
            position_side=self.position_side,
            open_quantity=self.open_quantity,
            average_entry_price=self.average_entry_price,
            realized_pnl_total=self.realized_pnl_total,
            accrued_swap_total=self.accrued_swap_total,
            highest_application_seq=self.highest_application_seq,
            highest_ledger_entry_seq=self.highest_ledger_entry_seq,
        )

    def __post_init__(self) -> None:
        if type(self.paper_position_snapshot_id) is not str:
            raise TypeError("paper_position_snapshot_id must be exact str")
        if (
            type(self.snapshot_contract_version) is not str
            or self.snapshot_contract_version != PAPER_POSITION_SNAPSHOT_CONTRACT_VERSION
        ):
            raise ValueError("unsupported position snapshot contract")
        _text(self.paper_account_id, "paper_account_id")
        _text(self.paper_position_id, "paper_position_id")
        _exact_pair(self.pair)
        _exact_position_side(self.position_side)
        _nonnegative_finite_decimal(self.open_quantity, "open_quantity")
        _positive_finite_decimal(self.average_entry_price, "average_entry_price")
        _finite_decimal(self.realized_pnl_total, "realized_pnl_total")
        _finite_decimal(self.accrued_swap_total, "accrued_swap_total")
        _nonnegative_int(self.highest_application_seq, "highest_application_seq")
        _nonnegative_int(self.highest_ledger_entry_seq, "highest_ledger_entry_seq")
        if self.average_entry_price_formula_version != PAPER_WEIGHTED_AVERAGE_ENTRY_PRICE_V1:
            raise ValueError("unsupported average_entry_price_formula_version")
        if self.realized_pnl_formula_version != PAPER_REALIZED_PNL_V1:
            raise ValueError("unsupported realized_pnl_formula_version")
        if self.swap_accrual_formula_version != PAPER_SWAP_ACCRUAL_V1:
            raise ValueError("unsupported swap_accrual_formula_version")
        _utc(self.created_at, "created_at")
        expected_id = "paper-position-snapshot-" + digest(self.identity_payload)
        if self.paper_position_snapshot_id != expected_id:
            raise ValueError("paper_position_snapshot_id does not match content")


def _position_snapshot_payload(
    *,
    paper_account_id: str,
    paper_position_id: str,
    pair: CurrencyPair,
    position_side: PaperPositionSide,
    open_quantity: Decimal,
    average_entry_price: Decimal,
    realized_pnl_total: Decimal,
    accrued_swap_total: Decimal,
    highest_application_seq: int,
    highest_ledger_entry_seq: int,
) -> dict[str, object]:
    return {
        "snapshot_contract_version": PAPER_POSITION_SNAPSHOT_CONTRACT_VERSION,
        "paper_account_id": paper_account_id,
        "paper_position_id": paper_position_id,
        "pair": pair.symbol,
        "position_side": position_side.value,
        "open_quantity": str(open_quantity),
        "average_entry_price": str(average_entry_price),
        "realized_pnl_total": str(realized_pnl_total),
        "accrued_swap_total": str(accrued_swap_total),
        "highest_application_seq": highest_application_seq,
        "highest_ledger_entry_seq": highest_ledger_entry_seq,
        "average_entry_price_formula_version": PAPER_WEIGHTED_AVERAGE_ENTRY_PRICE_V1,
        "realized_pnl_formula_version": PAPER_REALIZED_PNL_V1,
        "swap_accrual_formula_version": PAPER_SWAP_ACCRUAL_V1,
    }


_ACCOUNT_SNAPSHOT_FORMULA_VERSIONS = (
    PAPER_ACCOUNT_MARK_SET_V1,
    PAPER_UNREALIZED_PNL_V1,
    PAPER_GROSS_EXPOSURE_V1,
    PAPER_ACCOUNT_EQUITY_V1,
    PAPER_USED_MARGIN_V1,
    PAPER_AVAILABLE_MARGIN_V1,
    PAPER_OPEN_POSITION_COUNT_V1,
    PAPER_OPEN_ORDER_COUNT_V1,
)


@dataclass(frozen=True, slots=True)
class PaperAccountSnapshot:
    paper_account_snapshot_id: str
    snapshot_contract_version: str
    paper_account_id: str
    cash: Decimal
    realized_pnl_total: Decimal
    unrealized_pnl_total: Decimal
    accrued_swap_total: Decimal
    equity: Decimal
    used_margin: Decimal
    available_margin: Decimal
    gross_exposure: Decimal
    open_position_count: int
    open_order_count: int
    mark_observation_ids: tuple[str, ...]
    highest_application_seq: int
    highest_ledger_entry_seq: int
    highest_order_event_seq: int
    margin_policy_version: str
    unrealized_mark_policy_version: str
    formula_versions: tuple[str, ...]
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        paper_account_id: str,
        cash: Decimal,
        realized_pnl_total: Decimal,
        unrealized_pnl_total: Decimal,
        accrued_swap_total: Decimal,
        equity: Decimal,
        used_margin: Decimal,
        available_margin: Decimal,
        gross_exposure: Decimal,
        open_position_count: int,
        open_order_count: int,
        mark_observation_ids: tuple[str, ...],
        highest_application_seq: int,
        highest_ledger_entry_seq: int,
        highest_order_event_seq: int,
        margin_policy_version: str,
        unrealized_mark_policy_version: str,
        created_at: datetime,
    ) -> PaperAccountSnapshot:
        payload = _account_snapshot_payload(
            paper_account_id=paper_account_id,
            cash=cash,
            realized_pnl_total=realized_pnl_total,
            unrealized_pnl_total=unrealized_pnl_total,
            accrued_swap_total=accrued_swap_total,
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
            margin_policy_version=margin_policy_version,
            unrealized_mark_policy_version=unrealized_mark_policy_version,
        )
        return cls(
            "paper-account-snapshot-" + digest(payload),
            PAPER_ACCOUNT_SNAPSHOT_CONTRACT_VERSION,
            paper_account_id,
            cash,
            realized_pnl_total,
            unrealized_pnl_total,
            accrued_swap_total,
            equity,
            used_margin,
            available_margin,
            gross_exposure,
            open_position_count,
            open_order_count,
            mark_observation_ids,
            highest_application_seq,
            highest_ledger_entry_seq,
            highest_order_event_seq,
            margin_policy_version,
            unrealized_mark_policy_version,
            _ACCOUNT_SNAPSHOT_FORMULA_VERSIONS,
            created_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _account_snapshot_payload(
            paper_account_id=self.paper_account_id,
            cash=self.cash,
            realized_pnl_total=self.realized_pnl_total,
            unrealized_pnl_total=self.unrealized_pnl_total,
            accrued_swap_total=self.accrued_swap_total,
            equity=self.equity,
            used_margin=self.used_margin,
            available_margin=self.available_margin,
            gross_exposure=self.gross_exposure,
            open_position_count=self.open_position_count,
            open_order_count=self.open_order_count,
            mark_observation_ids=self.mark_observation_ids,
            highest_application_seq=self.highest_application_seq,
            highest_ledger_entry_seq=self.highest_ledger_entry_seq,
            highest_order_event_seq=self.highest_order_event_seq,
            margin_policy_version=self.margin_policy_version,
            unrealized_mark_policy_version=self.unrealized_mark_policy_version,
        )

    def __post_init__(self) -> None:
        if type(self.paper_account_snapshot_id) is not str:
            raise TypeError("paper_account_snapshot_id must be exact str")
        if (
            type(self.snapshot_contract_version) is not str
            or self.snapshot_contract_version != PAPER_ACCOUNT_SNAPSHOT_CONTRACT_VERSION
        ):
            raise ValueError("unsupported account snapshot contract")
        _text(self.paper_account_id, "paper_account_id")
        _positive_finite_decimal(self.cash, "cash")
        _finite_decimal(self.realized_pnl_total, "realized_pnl_total")
        _finite_decimal(self.unrealized_pnl_total, "unrealized_pnl_total")
        _finite_decimal(self.accrued_swap_total, "accrued_swap_total")
        _finite_decimal(self.equity, "equity")
        _nonnegative_finite_decimal(self.used_margin, "used_margin")
        _finite_decimal(self.available_margin, "available_margin")
        _nonnegative_finite_decimal(self.gross_exposure, "gross_exposure")
        _nonnegative_int(self.open_position_count, "open_position_count")
        _nonnegative_int(self.open_order_count, "open_order_count")
        _ordered_str_tuple(self.mark_observation_ids, "mark_observation_ids")
        _nonnegative_int(self.highest_application_seq, "highest_application_seq")
        _nonnegative_int(self.highest_ledger_entry_seq, "highest_ledger_entry_seq")
        _nonnegative_int(self.highest_order_event_seq, "highest_order_event_seq")
        _text(self.margin_policy_version, "margin_policy_version")
        _text(self.unrealized_mark_policy_version, "unrealized_mark_policy_version")
        if tuple(self.formula_versions) != _ACCOUNT_SNAPSHOT_FORMULA_VERSIONS:
            raise ValueError("unsupported formula_versions")
        _utc(self.created_at, "created_at")
        expected_id = "paper-account-snapshot-" + digest(self.identity_payload)
        if self.paper_account_snapshot_id != expected_id:
            raise ValueError("paper_account_snapshot_id does not match content")


def _account_snapshot_payload(
    *,
    paper_account_id: str,
    cash: Decimal,
    realized_pnl_total: Decimal,
    unrealized_pnl_total: Decimal,
    accrued_swap_total: Decimal,
    equity: Decimal,
    used_margin: Decimal,
    available_margin: Decimal,
    gross_exposure: Decimal,
    open_position_count: int,
    open_order_count: int,
    mark_observation_ids: tuple[str, ...],
    highest_application_seq: int,
    highest_ledger_entry_seq: int,
    highest_order_event_seq: int,
    margin_policy_version: str,
    unrealized_mark_policy_version: str,
) -> dict[str, object]:
    return {
        "snapshot_contract_version": PAPER_ACCOUNT_SNAPSHOT_CONTRACT_VERSION,
        "paper_account_id": paper_account_id,
        "cash": str(cash),
        "realized_pnl_total": str(realized_pnl_total),
        "unrealized_pnl_total": str(unrealized_pnl_total),
        "accrued_swap_total": str(accrued_swap_total),
        "equity": str(equity),
        "used_margin": str(used_margin),
        "available_margin": str(available_margin),
        "gross_exposure": str(gross_exposure),
        "open_position_count": open_position_count,
        "open_order_count": open_order_count,
        "mark_observation_ids": list(mark_observation_ids),
        "highest_application_seq": highest_application_seq,
        "highest_ledger_entry_seq": highest_ledger_entry_seq,
        "highest_order_event_seq": highest_order_event_seq,
        "margin_policy_version": margin_policy_version,
        "unrealized_mark_policy_version": unrealized_mark_policy_version,
        "formula_versions": list(_ACCOUNT_SNAPSHOT_FORMULA_VERSIONS),
    }


# ---------------------------------------------------------------------------
# Swap accrual
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PaperSwapAccrualPolicy:
    policy_contract_version: str
    policy_version: str
    formula_version: str
    unit_basis_base_units: tuple[tuple[str, Decimal], ...]
    maximum_swap_age: timedelta
    settlement_currency: Currency

    @classmethod
    def create(
        cls,
        *,
        policy_version: str,
        unit_basis_base_units: tuple[tuple[str, Decimal], ...],
        maximum_swap_age: timedelta,
        settlement_currency: Currency,
    ) -> PaperSwapAccrualPolicy:
        return cls(
            PAPER_SWAP_ACCRUAL_POLICY_CONTRACT_VERSION,
            policy_version,
            PAPER_SWAP_ACCRUAL_V1,
            unit_basis_base_units,
            maximum_swap_age,
            settlement_currency,
        )

    def base_units_for(self, unit_basis: str) -> Decimal | None:
        for key, value in self.unit_basis_base_units:
            if key == unit_basis:
                return value
        return None

    def __post_init__(self) -> None:
        if (
            type(self.policy_contract_version) is not str
            or self.policy_contract_version != PAPER_SWAP_ACCRUAL_POLICY_CONTRACT_VERSION
        ):
            raise ValueError("unsupported swap accrual policy contract")
        _text(self.policy_version, "policy_version")
        if self.formula_version != PAPER_SWAP_ACCRUAL_V1:
            raise ValueError("formula_version must be exact paper-swap-accrual-v1")
        if type(self.unit_basis_base_units) is not tuple or not self.unit_basis_base_units:
            raise ValueError("unit_basis_base_units must be a non-empty exact tuple")
        seen_keys: set[str] = set()
        for entry in self.unit_basis_base_units:
            if type(entry) is not tuple or len(entry) != 2:
                raise TypeError("unit_basis_base_units entries must be exact (str, Decimal) tuples")
            key, value = entry
            _text(key, "unit_basis_base_units key")
            if key in seen_keys:
                raise ValueError("unit_basis_base_units keys must be unique")
            seen_keys.add(key)
            _positive_finite_decimal(value, "unit_basis_base_units value")
        _positive_timedelta(self.maximum_swap_age, "maximum_swap_age")
        _exact_jpy_currency(self.settlement_currency, "settlement_currency")


def paper_swap_rollover_instant_v1(rollover_date: date) -> datetime:
    if type(rollover_date) is not date:
        raise TypeError("rollover_date must be exact datetime.date")
    return datetime(
        rollover_date.year, rollover_date.month, rollover_date.day, 0, 0, 0, 0, tzinfo=UTC
    )


def paper_swap_accrual_v1(
    *,
    open_quantity: Decimal,
    received_amount: Decimal,
    base_units_per_unit: Decimal,
) -> Decimal:
    _positive_finite_decimal(open_quantity, "open_quantity")
    _finite_decimal(received_amount, "received_amount")
    _positive_finite_decimal(base_units_per_unit, "base_units_per_unit")
    with decimal.localcontext(PAPER_QUOTIENT_ARITHMETIC_V1):
        quantity_ratio = open_quantity / base_units_per_unit
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        return received_amount * quantity_ratio


class PaperSwapAccrualOutcome(StrEnum):
    ACCRUED = "ACCRUED"
    NOT_ACCRUED_SWAP_MISSING = "NOT_ACCRUED_SWAP_MISSING"
    NOT_ACCRUED_SWAP_UNAVAILABLE = "NOT_ACCRUED_SWAP_UNAVAILABLE"
    NOT_ACCRUED_SWAP_STALE = "NOT_ACCRUED_SWAP_STALE"
    NOT_ACCRUED_UNSUPPORTED_UNIT_BASIS = "NOT_ACCRUED_UNSUPPORTED_UNIT_BASIS"
    NOT_ACCRUED_UNSUPPORTED_SETTLEMENT_CURRENCY = "NOT_ACCRUED_UNSUPPORTED_SETTLEMENT_CURRENCY"
    NOT_ACCRUED_PAIR_MISMATCH = "NOT_ACCRUED_PAIR_MISMATCH"
    NOT_ACCRUED_POSITION_NOT_OPEN = "NOT_ACCRUED_POSITION_NOT_OPEN"


def paper_swap_accrual_outcome_v1(
    *,
    evidence: OperationalSwapEvidence | None,
    position_pair: CurrencyPair,
    open_quantity: Decimal,
    policy: PaperSwapAccrualPolicy,
    rollover_at: datetime,
) -> PaperSwapAccrualOutcome:
    _exact_pair(position_pair, "position_pair")
    if type(open_quantity) is not Decimal or not open_quantity.is_finite():
        raise ValueError("open_quantity must be a finite Decimal")
    if type(policy) is not PaperSwapAccrualPolicy:
        raise TypeError("policy must be exact PaperSwapAccrualPolicy")
    _utc(rollover_at, "rollover_at")
    if evidence is None:
        return PaperSwapAccrualOutcome.NOT_ACCRUED_SWAP_MISSING
    if type(evidence) is not OperationalSwapEvidence:
        raise TypeError("evidence must be exact OperationalSwapEvidence or None")
    if evidence.pair != position_pair:
        return PaperSwapAccrualOutcome.NOT_ACCRUED_PAIR_MISMATCH
    if evidence.availability is not SwapAvailability.AVAILABLE:
        return PaperSwapAccrualOutcome.NOT_ACCRUED_SWAP_UNAVAILABLE
    if evidence.settlement_currency != _JPY:
        return PaperSwapAccrualOutcome.NOT_ACCRUED_UNSUPPORTED_SETTLEMENT_CURRENCY
    assert evidence.unit_basis is not None  # guaranteed by AVAILABLE evidence's own validation
    if policy.base_units_for(evidence.unit_basis) is None:
        return PaperSwapAccrualOutcome.NOT_ACCRUED_UNSUPPORTED_UNIT_BASIS
    if rollover_at < evidence.effective_from:
        return PaperSwapAccrualOutcome.NOT_ACCRUED_SWAP_STALE
    if evidence.effective_until is not None and rollover_at > evidence.effective_until:
        return PaperSwapAccrualOutcome.NOT_ACCRUED_SWAP_STALE
    if rollover_at < evidence.received_at:
        return PaperSwapAccrualOutcome.NOT_ACCRUED_SWAP_STALE
    if rollover_at - evidence.received_at > policy.maximum_swap_age:
        return PaperSwapAccrualOutcome.NOT_ACCRUED_SWAP_STALE
    if open_quantity <= 0:
        return PaperSwapAccrualOutcome.NOT_ACCRUED_POSITION_NOT_OPEN
    return PaperSwapAccrualOutcome.ACCRUED


@dataclass(frozen=True, slots=True)
class PaperSwapAccrual:
    paper_swap_accrual_id: str
    accrual_contract_version: str
    paper_position_id: str
    paper_position_snapshot_id: str
    swap_evidence_id: str
    rollover_date: date
    open_quantity: Decimal
    unit_basis: str
    base_units_per_unit: Decimal
    settlement_currency: Currency
    policy_version: str
    formula_version: str
    amount: Decimal
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        paper_position_id: str,
        paper_position_snapshot_id: str,
        swap_evidence_id: str,
        rollover_date: date,
        open_quantity: Decimal,
        unit_basis: str,
        base_units_per_unit: Decimal,
        settlement_currency: Currency,
        policy_version: str,
        amount: Decimal,
        created_at: datetime,
    ) -> PaperSwapAccrual:
        payload = _swap_accrual_payload(
            paper_position_id=paper_position_id,
            paper_position_snapshot_id=paper_position_snapshot_id,
            swap_evidence_id=swap_evidence_id,
            rollover_date=rollover_date,
            open_quantity=open_quantity,
            unit_basis=unit_basis,
            base_units_per_unit=base_units_per_unit,
            settlement_currency=settlement_currency,
            policy_version=policy_version,
            amount=amount,
        )
        return cls(
            "paper-swap-accrual-" + digest(payload),
            PAPER_SWAP_ACCRUAL_RECORD_CONTRACT_VERSION,
            paper_position_id,
            paper_position_snapshot_id,
            swap_evidence_id,
            rollover_date,
            open_quantity,
            unit_basis,
            base_units_per_unit,
            settlement_currency,
            policy_version,
            PAPER_SWAP_ACCRUAL_V1,
            amount,
            created_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _swap_accrual_payload(
            paper_position_id=self.paper_position_id,
            paper_position_snapshot_id=self.paper_position_snapshot_id,
            swap_evidence_id=self.swap_evidence_id,
            rollover_date=self.rollover_date,
            open_quantity=self.open_quantity,
            unit_basis=self.unit_basis,
            base_units_per_unit=self.base_units_per_unit,
            settlement_currency=self.settlement_currency,
            policy_version=self.policy_version,
            amount=self.amount,
        )

    def __post_init__(self) -> None:
        if type(self.paper_swap_accrual_id) is not str:
            raise TypeError("paper_swap_accrual_id must be exact str")
        if (
            type(self.accrual_contract_version) is not str
            or self.accrual_contract_version != PAPER_SWAP_ACCRUAL_RECORD_CONTRACT_VERSION
        ):
            raise ValueError("unsupported swap accrual contract")
        for value, label in (
            (self.paper_position_id, "paper_position_id"),
            (self.paper_position_snapshot_id, "paper_position_snapshot_id"),
            (self.swap_evidence_id, "swap_evidence_id"),
            (self.unit_basis, "unit_basis"),
            (self.policy_version, "policy_version"),
        ):
            _text(value, label)
        if type(self.rollover_date) is not date:
            raise TypeError("rollover_date must be exact datetime.date")
        _positive_finite_decimal(self.open_quantity, "open_quantity")
        _positive_finite_decimal(self.base_units_per_unit, "base_units_per_unit")
        _exact_jpy_currency(self.settlement_currency, "settlement_currency")
        if self.formula_version != PAPER_SWAP_ACCRUAL_V1:
            raise ValueError("formula_version must be exact paper-swap-accrual-v1")
        _finite_decimal(self.amount, "amount")
        _utc(self.created_at, "created_at")
        expected_id = "paper-swap-accrual-" + digest(self.identity_payload)
        if self.paper_swap_accrual_id != expected_id:
            raise ValueError("paper_swap_accrual_id does not match content")


def _swap_accrual_payload(
    *,
    paper_position_id: str,
    paper_position_snapshot_id: str,
    swap_evidence_id: str,
    rollover_date: date,
    open_quantity: Decimal,
    unit_basis: str,
    base_units_per_unit: Decimal,
    settlement_currency: Currency,
    policy_version: str,
    amount: Decimal,
) -> dict[str, object]:
    return {
        "accrual_contract_version": PAPER_SWAP_ACCRUAL_RECORD_CONTRACT_VERSION,
        "paper_position_id": paper_position_id,
        "paper_position_snapshot_id": paper_position_snapshot_id,
        "swap_evidence_id": swap_evidence_id,
        "rollover_date": rollover_date.isoformat(),
        "open_quantity": str(open_quantity),
        "unit_basis": unit_basis,
        "base_units_per_unit": str(base_units_per_unit),
        "settlement_currency": settlement_currency.code,
        "policy_version": policy_version,
        "formula_version": PAPER_SWAP_ACCRUAL_V1,
        "amount": str(amount),
    }


@dataclass(frozen=True, slots=True)
class PaperSwapNonAccrual:
    paper_swap_non_accrual_id: str
    non_accrual_contract_version: str
    paper_position_id: str
    paper_position_snapshot_id: str
    swap_evidence_id: str | None
    rollover_date: date
    outcome: PaperSwapAccrualOutcome
    policy_version: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        paper_position_id: str,
        paper_position_snapshot_id: str,
        swap_evidence_id: str | None,
        rollover_date: date,
        outcome: PaperSwapAccrualOutcome,
        policy_version: str,
        created_at: datetime,
    ) -> PaperSwapNonAccrual:
        payload = _swap_non_accrual_payload(
            paper_position_id=paper_position_id,
            paper_position_snapshot_id=paper_position_snapshot_id,
            swap_evidence_id=swap_evidence_id,
            rollover_date=rollover_date,
            outcome=outcome,
            policy_version=policy_version,
        )
        return cls(
            "paper-swap-non-accrual-" + digest(payload),
            PAPER_SWAP_NON_ACCRUAL_CONTRACT_VERSION,
            paper_position_id,
            paper_position_snapshot_id,
            swap_evidence_id,
            rollover_date,
            outcome,
            policy_version,
            created_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _swap_non_accrual_payload(
            paper_position_id=self.paper_position_id,
            paper_position_snapshot_id=self.paper_position_snapshot_id,
            swap_evidence_id=self.swap_evidence_id,
            rollover_date=self.rollover_date,
            outcome=self.outcome,
            policy_version=self.policy_version,
        )

    def __post_init__(self) -> None:
        if type(self.paper_swap_non_accrual_id) is not str:
            raise TypeError("paper_swap_non_accrual_id must be exact str")
        if (
            type(self.non_accrual_contract_version) is not str
            or self.non_accrual_contract_version != PAPER_SWAP_NON_ACCRUAL_CONTRACT_VERSION
        ):
            raise ValueError("unsupported swap non-accrual contract")
        for value, label in (
            (self.paper_position_id, "paper_position_id"),
            (self.paper_position_snapshot_id, "paper_position_snapshot_id"),
            (self.policy_version, "policy_version"),
        ):
            _text(value, label)
        if self.swap_evidence_id is not None:
            _text(self.swap_evidence_id, "swap_evidence_id")
        if type(self.rollover_date) is not date:
            raise TypeError("rollover_date must be exact datetime.date")
        if type(self.outcome) is not PaperSwapAccrualOutcome:
            raise TypeError("outcome must be exact PaperSwapAccrualOutcome")
        if self.outcome is PaperSwapAccrualOutcome.ACCRUED:
            raise ValueError("PaperSwapNonAccrual outcome must not be ACCRUED")
        _utc(self.created_at, "created_at")
        expected_id = "paper-swap-non-accrual-" + digest(self.identity_payload)
        if self.paper_swap_non_accrual_id != expected_id:
            raise ValueError("paper_swap_non_accrual_id does not match content")


def _swap_non_accrual_payload(
    *,
    paper_position_id: str,
    paper_position_snapshot_id: str,
    swap_evidence_id: str | None,
    rollover_date: date,
    outcome: PaperSwapAccrualOutcome,
    policy_version: str,
) -> dict[str, object]:
    return {
        "non_accrual_contract_version": PAPER_SWAP_NON_ACCRUAL_CONTRACT_VERSION,
        "paper_position_id": paper_position_id,
        "paper_position_snapshot_id": paper_position_snapshot_id,
        "swap_evidence_id": swap_evidence_id,
        "rollover_date": rollover_date.isoformat(),
        "outcome": outcome.value,
        "policy_version": policy_version,
    }


def evaluate_paper_swap_accrual(
    *,
    evidence: OperationalSwapEvidence | None,
    paper_position_id: str,
    paper_position_snapshot_id: str,
    position_pair: CurrencyPair,
    position_side: PaperPositionSide,
    open_quantity: Decimal,
    policy: PaperSwapAccrualPolicy,
    rollover_date: date,
    created_at: datetime,
) -> PaperSwapAccrual | PaperSwapNonAccrual:
    _exact_position_side(position_side)
    rollover_at = paper_swap_rollover_instant_v1(rollover_date)
    outcome = paper_swap_accrual_outcome_v1(
        evidence=evidence,
        position_pair=position_pair,
        open_quantity=open_quantity,
        policy=policy,
        rollover_at=rollover_at,
    )
    if outcome is not PaperSwapAccrualOutcome.ACCRUED:
        return PaperSwapNonAccrual.create(
            paper_position_id=paper_position_id,
            paper_position_snapshot_id=paper_position_snapshot_id,
            swap_evidence_id=None if evidence is None else evidence.swap_evidence_id,
            rollover_date=rollover_date,
            outcome=outcome,
            policy_version=policy.policy_version,
            created_at=created_at,
        )
    assert evidence is not None
    received_amount = (
        evidence.long_received_amount
        if position_side is PaperPositionSide.LONG
        else evidence.short_received_amount
    )
    assert received_amount is not None
    assert evidence.unit_basis is not None
    base_units_per_unit = policy.base_units_for(evidence.unit_basis)
    assert base_units_per_unit is not None
    amount = paper_swap_accrual_v1(
        open_quantity=open_quantity,
        received_amount=received_amount,
        base_units_per_unit=base_units_per_unit,
    )
    return PaperSwapAccrual.create(
        paper_position_id=paper_position_id,
        paper_position_snapshot_id=paper_position_snapshot_id,
        swap_evidence_id=evidence.swap_evidence_id,
        rollover_date=rollover_date,
        open_quantity=open_quantity,
        unit_basis=evidence.unit_basis,
        base_units_per_unit=base_units_per_unit,
        settlement_currency=evidence.settlement_currency or _JPY,
        policy_version=policy.policy_version,
        amount=amount,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# Swap accrual corrections
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PaperSwapAccrualCorrection:
    correction_id: str
    correction_contract_version: str
    corrected_accrual_id: str
    chain_ordinal: int
    predecessor_correction_id: str | None
    effective_amount_before: Decimal
    replacement_amount: Decimal
    delta_amount: Decimal
    correction_reason: str
    swap_evidence_id: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        corrected_accrual_id: str,
        chain_ordinal: int,
        predecessor_correction_id: str | None,
        effective_amount_before: Decimal,
        replacement_amount: Decimal,
        correction_reason: str,
        swap_evidence_id: str,
        created_at: datetime,
    ) -> PaperSwapAccrualCorrection:
        _finite_decimal(effective_amount_before, "effective_amount_before")
        _finite_decimal(replacement_amount, "replacement_amount")
        with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
            delta_amount = replacement_amount - effective_amount_before
        payload = _swap_correction_payload(
            corrected_accrual_id=corrected_accrual_id,
            chain_ordinal=chain_ordinal,
            predecessor_correction_id=predecessor_correction_id,
            effective_amount_before=effective_amount_before,
            replacement_amount=replacement_amount,
            delta_amount=delta_amount,
            correction_reason=correction_reason,
            swap_evidence_id=swap_evidence_id,
        )
        return cls(
            "paper-swap-correction-" + digest(payload),
            PAPER_SWAP_ACCRUAL_CORRECTION_CONTRACT_VERSION,
            corrected_accrual_id,
            chain_ordinal,
            predecessor_correction_id,
            effective_amount_before,
            replacement_amount,
            delta_amount,
            correction_reason,
            swap_evidence_id,
            created_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _swap_correction_payload(
            corrected_accrual_id=self.corrected_accrual_id,
            chain_ordinal=self.chain_ordinal,
            predecessor_correction_id=self.predecessor_correction_id,
            effective_amount_before=self.effective_amount_before,
            replacement_amount=self.replacement_amount,
            delta_amount=self.delta_amount,
            correction_reason=self.correction_reason,
            swap_evidence_id=self.swap_evidence_id,
        )

    def __post_init__(self) -> None:
        if type(self.correction_id) is not str:
            raise TypeError("correction_id must be exact str")
        if (
            type(self.correction_contract_version) is not str
            or self.correction_contract_version != PAPER_SWAP_ACCRUAL_CORRECTION_CONTRACT_VERSION
        ):
            raise ValueError("unsupported swap accrual correction contract")
        for value, label in (
            (self.corrected_accrual_id, "corrected_accrual_id"),
            (self.correction_reason, "correction_reason"),
            (self.swap_evidence_id, "swap_evidence_id"),
        ):
            _text(value, label)
        if (
            type(self.chain_ordinal) is not int
            or isinstance(self.chain_ordinal, bool)
            or self.chain_ordinal < 1
        ):
            raise ValueError("chain_ordinal must be an exact int >= 1")
        if self.chain_ordinal == 1:
            if self.predecessor_correction_id is not None:
                raise ValueError("chain_ordinal 1 must carry predecessor_correction_id None")
        else:
            _text(self.predecessor_correction_id, "predecessor_correction_id")
        _finite_decimal(self.effective_amount_before, "effective_amount_before")
        _finite_decimal(self.replacement_amount, "replacement_amount")
        _finite_decimal(self.delta_amount, "delta_amount")
        with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
            expected_delta = self.replacement_amount - self.effective_amount_before
        if self.delta_amount != expected_delta:
            raise ValueError("delta_amount must equal replacement_amount - effective_amount_before")
        _utc(self.created_at, "created_at")
        expected_id = "paper-swap-correction-" + digest(self.identity_payload)
        if self.correction_id != expected_id:
            raise ValueError("correction_id does not match content")


def _swap_correction_payload(
    *,
    corrected_accrual_id: str,
    chain_ordinal: int,
    predecessor_correction_id: str | None,
    effective_amount_before: Decimal,
    replacement_amount: Decimal,
    delta_amount: Decimal,
    correction_reason: str,
    swap_evidence_id: str,
) -> dict[str, object]:
    return {
        "correction_contract_version": PAPER_SWAP_ACCRUAL_CORRECTION_CONTRACT_VERSION,
        "corrected_accrual_id": corrected_accrual_id,
        "chain_ordinal": chain_ordinal,
        "predecessor_correction_id": predecessor_correction_id,
        "effective_amount_before": str(effective_amount_before),
        "replacement_amount": str(replacement_amount),
        "delta_amount": str(delta_amount),
        "correction_reason": correction_reason,
        "swap_evidence_id": swap_evidence_id,
    }


def compute_effective_amount_before(
    original_amount: Decimal,
    prior_corrections: Sequence[PaperSwapAccrualCorrection],
) -> Decimal:
    _finite_decimal(original_amount, "original_amount")
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        total = original_amount
        for correction in prior_corrections:
            if type(correction) is not PaperSwapAccrualCorrection:
                raise TypeError(
                    "prior_corrections entries must be exact PaperSwapAccrualCorrection"
                )
            total = total + correction.delta_amount
    return total


def validate_correction_chain(
    existing_chain: Sequence[PaperSwapAccrualCorrection],
    *,
    chain_ordinal: int,
    predecessor_correction_id: str | None,
) -> None:
    existing = tuple(existing_chain)
    for correction in existing:
        if type(correction) is not PaperSwapAccrualCorrection:
            raise TypeError("existing_chain entries must be exact PaperSwapAccrualCorrection")
    expected_ordinal = len(existing) + 1
    if chain_ordinal != expected_ordinal:
        raise PaperLedgerIntegrityError(
            f"chain_ordinal must equal len(existing chain) + 1 ({expected_ordinal})"
        )
    if expected_ordinal == 1:
        if predecessor_correction_id is not None:
            raise PaperLedgerIntegrityError(
                "chain_ordinal 1 must carry predecessor_correction_id None"
            )
        return
    last = existing[-1]
    if predecessor_correction_id != last.correction_id:
        raise PaperLedgerIntegrityError(
            "predecessor_correction_id must equal the chain's current last correction_id"
        )


def next_swap_accrual_correction(
    *,
    original_accrual: PaperSwapAccrual,
    existing_chain: Sequence[PaperSwapAccrualCorrection],
    chain_ordinal: int,
    predecessor_correction_id: str | None,
    replacement_amount: Decimal,
    correction_reason: str,
    swap_evidence_id: str,
    created_at: datetime,
) -> PaperSwapAccrualCorrection:
    if type(original_accrual) is not PaperSwapAccrual:
        raise TypeError("original_accrual must be exact PaperSwapAccrual")
    validate_correction_chain(
        existing_chain,
        chain_ordinal=chain_ordinal,
        predecessor_correction_id=predecessor_correction_id,
    )
    effective_amount_before = compute_effective_amount_before(
        original_accrual.amount, existing_chain
    )
    return PaperSwapAccrualCorrection.create(
        corrected_accrual_id=original_accrual.paper_swap_accrual_id,
        chain_ordinal=chain_ordinal,
        predecessor_correction_id=predecessor_correction_id,
        effective_amount_before=effective_amount_before,
        replacement_amount=replacement_amount,
        correction_reason=correction_reason,
        swap_evidence_id=swap_evidence_id,
        created_at=created_at,
    )


def validate_swap_accrual_correction(
    correction: PaperSwapAccrualCorrection,
    *,
    original_accrual: PaperSwapAccrual,
    existing_chain: Sequence[PaperSwapAccrualCorrection],
) -> None:
    if type(correction) is not PaperSwapAccrualCorrection:
        raise TypeError("correction must be exact PaperSwapAccrualCorrection")
    if type(original_accrual) is not PaperSwapAccrual:
        raise TypeError("original_accrual must be exact PaperSwapAccrual")
    if correction.corrected_accrual_id != original_accrual.paper_swap_accrual_id:
        raise PaperLedgerIntegrityError("correction does not reference the supplied accrual")
    validate_correction_chain(
        existing_chain,
        chain_ordinal=correction.chain_ordinal,
        predecessor_correction_id=correction.predecessor_correction_id,
    )
    expected_effective_before = compute_effective_amount_before(
        original_accrual.amount, existing_chain
    )
    if correction.effective_amount_before != expected_effective_before:
        raise PaperLedgerIntegrityError(
            "effective_amount_before disagrees with the recomputed chain total"
        )
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        expected_delta = correction.replacement_amount - expected_effective_before
    if correction.delta_amount != expected_delta:
        raise PaperLedgerIntegrityError("delta_amount disagrees with the recomputed value")


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


class PaperReconciledRecordKind(StrEnum):
    ACCOUNT_SNAPSHOT = "ACCOUNT_SNAPSHOT"
    LEDGER_ENTRY = "LEDGER_ENTRY"
    POSITION_FILL_APPLICATION = "POSITION_FILL_APPLICATION"
    POSITION_SNAPSHOT = "POSITION_SNAPSHOT"


class PaperReconciliationOutcome(StrEnum):
    MATCHED = "MATCHED"
    MISMATCHED = "MISMATCHED"


@dataclass(frozen=True, slots=True)
class PaperReconciliationResult:
    reconciliation_result_id: str
    result_contract_version: str
    paper_account_id: str
    outcome: PaperReconciliationOutcome
    reconciled_position_ids: tuple[str, ...]
    highest_application_seq: int
    highest_ledger_entry_seq: int
    highest_order_event_seq: int
    mismatched_record_kinds: tuple[PaperReconciledRecordKind, ...]
    mismatched_record_ids: tuple[str, ...]
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        paper_account_id: str,
        reconciled_position_ids: tuple[str, ...],
        highest_application_seq: int,
        highest_ledger_entry_seq: int,
        highest_order_event_seq: int,
        mismatched_record_kinds: tuple[PaperReconciledRecordKind, ...],
        mismatched_record_ids: tuple[str, ...],
        created_at: datetime,
    ) -> PaperReconciliationResult:
        outcome = (
            PaperReconciliationOutcome.MATCHED
            if not mismatched_record_kinds and not mismatched_record_ids
            else PaperReconciliationOutcome.MISMATCHED
        )
        payload = _reconciliation_payload(
            paper_account_id=paper_account_id,
            outcome=outcome,
            reconciled_position_ids=reconciled_position_ids,
            highest_application_seq=highest_application_seq,
            highest_ledger_entry_seq=highest_ledger_entry_seq,
            highest_order_event_seq=highest_order_event_seq,
            mismatched_record_kinds=mismatched_record_kinds,
            mismatched_record_ids=mismatched_record_ids,
        )
        return cls(
            "paper-reconciliation-" + digest(payload),
            PAPER_RECONCILIATION_RESULT_CONTRACT_VERSION,
            paper_account_id,
            outcome,
            reconciled_position_ids,
            highest_application_seq,
            highest_ledger_entry_seq,
            highest_order_event_seq,
            mismatched_record_kinds,
            mismatched_record_ids,
            created_at,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _reconciliation_payload(
            paper_account_id=self.paper_account_id,
            outcome=self.outcome,
            reconciled_position_ids=self.reconciled_position_ids,
            highest_application_seq=self.highest_application_seq,
            highest_ledger_entry_seq=self.highest_ledger_entry_seq,
            highest_order_event_seq=self.highest_order_event_seq,
            mismatched_record_kinds=self.mismatched_record_kinds,
            mismatched_record_ids=self.mismatched_record_ids,
        )

    def __post_init__(self) -> None:
        if type(self.reconciliation_result_id) is not str:
            raise TypeError("reconciliation_result_id must be exact str")
        if (
            type(self.result_contract_version) is not str
            or self.result_contract_version != PAPER_RECONCILIATION_RESULT_CONTRACT_VERSION
        ):
            raise ValueError("unsupported reconciliation result contract")
        _text(self.paper_account_id, "paper_account_id")
        if type(self.outcome) is not PaperReconciliationOutcome:
            raise TypeError("outcome must be exact PaperReconciliationOutcome")
        _ascending_unique(self.reconciled_position_ids, "reconciled_position_ids")
        for value, label in (
            (self.highest_application_seq, "highest_application_seq"),
            (self.highest_ledger_entry_seq, "highest_ledger_entry_seq"),
            (self.highest_order_event_seq, "highest_order_event_seq"),
        ):
            _nonnegative_int(value, label)
        for kind in self.mismatched_record_kinds:
            if type(kind) is not PaperReconciledRecordKind:
                raise TypeError(
                    "mismatched_record_kinds entries must be exact PaperReconciledRecordKind"
                )
        kind_values = [kind.value for kind in self.mismatched_record_kinds]
        _ascending_unique(kind_values, "mismatched_record_kinds")
        _ascending_unique(self.mismatched_record_ids, "mismatched_record_ids")
        expected_outcome = (
            PaperReconciliationOutcome.MATCHED
            if not self.mismatched_record_kinds and not self.mismatched_record_ids
            else PaperReconciliationOutcome.MISMATCHED
        )
        if self.outcome is not expected_outcome:
            raise ValueError("outcome must be MATCHED iff both mismatch tuples are empty")
        _utc(self.created_at, "created_at")
        expected_id = "paper-reconciliation-" + digest(self.identity_payload)
        if self.reconciliation_result_id != expected_id:
            raise ValueError("reconciliation_result_id does not match content")


def _reconciliation_payload(
    *,
    paper_account_id: str,
    outcome: PaperReconciliationOutcome,
    reconciled_position_ids: tuple[str, ...],
    highest_application_seq: int,
    highest_ledger_entry_seq: int,
    highest_order_event_seq: int,
    mismatched_record_kinds: tuple[PaperReconciledRecordKind, ...],
    mismatched_record_ids: tuple[str, ...],
) -> dict[str, object]:
    return {
        "result_contract_version": PAPER_RECONCILIATION_RESULT_CONTRACT_VERSION,
        "paper_account_id": paper_account_id,
        "outcome": outcome.value,
        "reconciled_position_ids": list(reconciled_position_ids),
        "highest_application_seq": highest_application_seq,
        "highest_ledger_entry_seq": highest_ledger_entry_seq,
        "highest_order_event_seq": highest_order_event_seq,
        "mismatched_record_kinds": [kind.value for kind in mismatched_record_kinds],
        "mismatched_record_ids": list(mismatched_record_ids),
    }


# ---------------------------------------------------------------------------
# Pure rebuild-and-compare functions, one per PaperReconciledRecordKind.
# ---------------------------------------------------------------------------


def _require_order(orders: Mapping[str, PaperOrder], paper_order_id: str) -> PaperOrder | None:
    order = orders.get(paper_order_id)
    if order is None:
        return None
    if type(order) is not PaperOrder:
        raise TypeError("orders values must be exact PaperOrder")
    return order


def rebuild_position_fill_applications(
    applications: Sequence[PaperPositionFillApplication],
    fills: Mapping[str, PaperFill],
    orders: Mapping[str, PaperOrder],
) -> tuple[str, ...]:
    ordered = tuple(applications)
    for application in ordered:
        if type(application) is not PaperPositionFillApplication:
            raise TypeError("applications entries must be exact PaperPositionFillApplication")
    if not ordered:
        return ()
    first_order = _require_order(orders, ordered[0].paper_order_id)
    if first_order is None:
        # An unresolvable paper_order_id is a typed mismatch, never a raise
        # (spec.md "Reconciliation": the transaction commits exactly one
        # result row in both outcomes). Without a resolvable first order,
        # position_side cannot be established, so no application in this
        # sequence can be reconciled.
        return tuple(application.paper_position_fill_application_id for application in ordered)
    position_side = (
        PaperPositionSide.LONG if first_order.side is Side.BUY else PaperPositionSide.SHORT
    )
    mismatched: list[str] = []
    for index, application in enumerate(ordered):
        order = _require_order(orders, application.paper_order_id)
        if order is None:
            mismatched.append(application.paper_position_fill_application_id)
            continue
        fill = fills[application.paper_fill_id]
        expected_position_id = order.intent_lineage.paper_position_id
        expected_kind = (
            PaperPositionApplicationKind.ENTRY
            if order.intent_lineage.intent_kind is PaperIntentKind.ENTRY
            else PaperPositionApplicationKind.REDUCE_ONLY
        )
        try:
            expected_open_after, expected_realized = compute_position_application_fields(
                ordered[:index],
                position_side=position_side,
                application_kind=application.application_kind,
                quantity=application.quantity,
                price=application.price,
            )
        except PaperLedgerIntegrityError:
            mismatched.append(application.paper_position_fill_application_id)
            continue
        regenerated_payload = _position_fill_application_payload(
            paper_position_id=expected_position_id,
            paper_order_id=application.paper_order_id,
            paper_fill_id=application.paper_fill_id,
            application_kind=expected_kind,
            quantity=fill.fill_quantity,
            price=fill.fill_price,
            open_quantity_after=expected_open_after,
            realized_pnl_amount=expected_realized,
        )
        regenerated_id = "paper-position-application-" + digest(regenerated_payload)
        if regenerated_id != application.paper_position_fill_application_id:
            mismatched.append(application.paper_position_fill_application_id)
    return tuple(mismatched)


def rebuild_ledger_entries(
    entries: Sequence[PaperLedgerEntry],
    *,
    realized_pnl_sources: Mapping[str, PaperPositionFillApplication],
    swap_accruals: Mapping[str, PaperSwapAccrual],
    swap_corrections: Mapping[str, PaperSwapAccrualCorrection],
    position_accounts: Mapping[str, str],
) -> tuple[str, ...]:
    mismatched: list[str] = []
    for entry in entries:
        if type(entry) is not PaperLedgerEntry:
            raise TypeError("entries entries must be exact PaperLedgerEntry")
        if entry.entry_kind is PaperLedgerEntryKind.REALIZED_PNL:
            application = realized_pnl_sources[entry.source_evidence_id]
            expected_amount = application.realized_pnl_amount
            expected_position_id = application.paper_position_id
        elif entry.entry_kind is PaperLedgerEntryKind.SWAP_ACCRUAL:
            accrual = swap_accruals[entry.source_evidence_id]
            expected_amount = accrual.amount
            expected_position_id = accrual.paper_position_id
        else:
            correction = swap_corrections[entry.source_evidence_id]
            expected_amount = correction.delta_amount
            expected_position_id = swap_accruals[correction.corrected_accrual_id].paper_position_id
        expected_account_id = position_accounts[expected_position_id]
        if (
            entry.amount != expected_amount
            or entry.paper_position_id != expected_position_id
            or entry.paper_account_id != expected_account_id
        ):
            mismatched.append(entry.ledger_entry_id)
    return tuple(mismatched)


def rebuild_position_snapshot(
    snapshot: PaperPositionSnapshot,
    *,
    paper_account_id: str,
    pair: CurrencyPair,
    position_side: PaperPositionSide,
    applications: Sequence[PaperPositionFillApplication],
    swap_ledger_entries: Sequence[PaperLedgerEntry],
    highest_application_seq: int,
    highest_ledger_entry_seq: int,
) -> bool:
    if type(snapshot) is not PaperPositionSnapshot:
        raise TypeError("snapshot must be exact PaperPositionSnapshot")
    ordered_applications = tuple(applications)
    try:
        open_quantity = project_paper_position_open_quantity(ordered_applications)
    except PaperLedgerIntegrityError:
        return False
    entry_applications = tuple(
        application
        for application in ordered_applications
        if application.application_kind is PaperPositionApplicationKind.ENTRY
    )
    average_entry_price = (
        paper_weighted_average_entry_price_v1(entry_applications)
        if entry_applications
        else snapshot.average_entry_price
    )
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        realized_pnl_total = Decimal(0)
        for application in ordered_applications:
            if application.realized_pnl_amount is not None:
                realized_pnl_total = realized_pnl_total + application.realized_pnl_amount
        accrued_swap_total = Decimal(0)
        for entry in swap_ledger_entries:
            if entry.entry_kind not in (
                PaperLedgerEntryKind.SWAP_ACCRUAL,
                PaperLedgerEntryKind.SWAP_ACCRUAL_CORRECTION,
            ):
                raise ValueError("swap_ledger_entries must only carry swap entry kinds")
            accrued_swap_total = accrued_swap_total + entry.amount
    regenerated_payload = _position_snapshot_payload(
        paper_account_id=paper_account_id,
        paper_position_id=snapshot.paper_position_id,
        pair=pair,
        position_side=position_side,
        open_quantity=open_quantity,
        average_entry_price=average_entry_price,
        realized_pnl_total=realized_pnl_total,
        accrued_swap_total=accrued_swap_total,
        highest_application_seq=highest_application_seq,
        highest_ledger_entry_seq=highest_ledger_entry_seq,
    )
    regenerated_id = "paper-position-snapshot-" + digest(regenerated_payload)
    return regenerated_id == snapshot.paper_position_snapshot_id


@dataclass(frozen=True, slots=True)
class PaperAccountSnapshotPositionInput:
    paper_position_id: str
    pair: CurrencyPair
    position_side: PaperPositionSide
    applications: tuple[PaperPositionFillApplication, ...]


def rebuild_account_snapshot(
    snapshot: PaperAccountSnapshot,
    *,
    bootstrap: PaperAccountBootstrap,
    positions: Sequence[PaperAccountSnapshotPositionInput],
    ledger_entries: Sequence[PaperLedgerEntry],
    observations_by_pair: Mapping[str, PaperMarketObservation],
    order_events_by_order: Mapping[str, Sequence[PaperOrderEvent]],
    highest_application_seq: int,
    highest_ledger_entry_seq: int,
    highest_order_event_seq: int,
) -> bool:
    if type(snapshot) is not PaperAccountSnapshot:
        raise TypeError("snapshot must be exact PaperAccountSnapshot")
    if type(bootstrap) is not PaperAccountBootstrap:
        raise TypeError("bootstrap must be exact PaperAccountBootstrap")

    open_quantities: dict[str, Decimal] = {}
    average_entry_prices: dict[str, Decimal] = {}
    for position in positions:
        try:
            open_quantities[position.paper_position_id] = project_paper_position_open_quantity(
                position.applications
            )
        except PaperLedgerIntegrityError:
            return False
        entry_applications = tuple(
            application
            for application in position.applications
            if application.application_kind is PaperPositionApplicationKind.ENTRY
        )
        if entry_applications:
            average_entry_prices[position.paper_position_id] = (
                paper_weighted_average_entry_price_v1(entry_applications)
            )

    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        realized_pnl_total = Decimal(0)
        accrued_swap_total = Decimal(0)
        for entry in ledger_entries:
            if entry.entry_kind is PaperLedgerEntryKind.REALIZED_PNL:
                realized_pnl_total = realized_pnl_total + entry.amount
            else:
                accrued_swap_total = accrued_swap_total + entry.amount

    open_position_marks: list[tuple[PaperPositionSide, Decimal, PaperMarketObservation]] = []
    for position in positions:
        open_quantity = open_quantities[position.paper_position_id]
        if open_quantity <= 0:
            continue
        observation = observations_by_pair[position.pair.symbol]
        open_position_marks.append((position.position_side, open_quantity, observation))

    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        unrealized_pnl_total = Decimal(0)
        for position in positions:
            open_quantity = open_quantities[position.paper_position_id]
            if open_quantity <= 0:
                continue
            observation = observations_by_pair[position.pair.symbol]
            unrealized_pnl_total = unrealized_pnl_total + paper_unrealized_pnl_v1(
                position_side=position.position_side,
                average_entry_price=average_entry_prices[position.paper_position_id],
                observation=observation,
                open_quantity=open_quantity,
            )

    gross_exposure = paper_gross_exposure_v1(open_position_marks)
    equity = paper_account_equity_v1(
        cash=bootstrap.initial_cash,
        realized_pnl_total=realized_pnl_total,
        accrued_swap_total=accrued_swap_total,
        unrealized_pnl_total=unrealized_pnl_total,
    )
    used_margin = paper_used_margin_v1(gross_exposure=gross_exposure, leverage=bootstrap.leverage)
    available_margin = paper_available_margin_v1(equity=equity, used_margin=used_margin)
    open_position_count = paper_open_position_count_v1(open_quantities)
    open_order_count = paper_open_order_count_v1(order_events_by_order)
    mark_observation_ids = tuple(
        observation.market_observation_id
        for observation in sorted(observations_by_pair.values(), key=lambda o: o.pair.symbol)
    )

    regenerated_payload = _account_snapshot_payload(
        paper_account_id=snapshot.paper_account_id,
        cash=bootstrap.initial_cash,
        realized_pnl_total=realized_pnl_total,
        unrealized_pnl_total=unrealized_pnl_total,
        accrued_swap_total=accrued_swap_total,
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
    )
    regenerated_id = "paper-account-snapshot-" + digest(regenerated_payload)
    return regenerated_id == snapshot.paper_account_snapshot_id
