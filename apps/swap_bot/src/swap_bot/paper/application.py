from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from fx_core import CurrencyPair
from fx_core.time import require_utc

from ..execution_authority import ExecutionAuthorityMode
from ..models import ApprovedExecutionIntent, ApprovedLiquidationIntent, Side
from ..strategy.ordinary_close import ApprovedCloseIntent
from .contracts import (
    PaperFillPolicy,
    PaperMarketObservation,
    PaperOrder,
    PaperOrderIntentLineage,
    PaperOrderState,
    opposite_side,
    project_paper_order_state,
)
from .ledger import PaperAccountBootstrap
from .store import AcceptedOrder, SQLitePaperStore, StepResolutionOutcome


class Clock(Protocol):
    """The sole time source on the application-service surface (spec.md "Frozen time source")."""

    def now(self) -> datetime: ...


class UTCClock:
    """The one thin production Clock adapter M3 permits."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class PaperApplicationDisposition(StrEnum):
    SHADOW_NOT_SUBMITTED = "SHADOW_NOT_SUBMITTED"
    PAPER_STEP_PENDING = "PAPER_STEP_PENDING"
    PAPER_STEP_RESOLVED = "PAPER_STEP_RESOLVED"


@dataclass(frozen=True, slots=True)
class PaperApplicationResult:
    disposition: PaperApplicationDisposition
    projected_order_state: PaperOrderState | None
    step_ordinal: int | None
    paper_fill_id: str | None
    reservation_consumption_id: str | None
    reservation_release_id: str | None

    @classmethod
    def shadow_not_submitted(cls) -> PaperApplicationResult:
        return cls(PaperApplicationDisposition.SHADOW_NOT_SUBMITTED, None, None, None, None, None)

    @classmethod
    def pending(
        cls, *, projected_order_state: PaperOrderState, step_ordinal: int
    ) -> PaperApplicationResult:
        return cls(
            PaperApplicationDisposition.PAPER_STEP_PENDING,
            projected_order_state,
            step_ordinal,
            None,
            None,
            None,
        )

    @classmethod
    def resolved(
        cls,
        *,
        projected_order_state: PaperOrderState,
        step_ordinal: int,
        paper_fill_id: str | None = None,
        reservation_consumption_id: str | None = None,
        reservation_release_id: str | None = None,
    ) -> PaperApplicationResult:
        return cls(
            PaperApplicationDisposition.PAPER_STEP_RESOLVED,
            projected_order_state,
            step_ordinal,
            paper_fill_id,
            reservation_consumption_id,
            reservation_release_id,
        )

    def __post_init__(self) -> None:
        if type(self.disposition) is not PaperApplicationDisposition:
            raise TypeError("disposition must be exact PaperApplicationDisposition")
        identifiers = (
            self.paper_fill_id,
            self.reservation_consumption_id,
            self.reservation_release_id,
        )
        if self.disposition is PaperApplicationDisposition.SHADOW_NOT_SUBMITTED:
            if self.projected_order_state is not None or self.step_ordinal is not None:
                raise ValueError(
                    "SHADOW_NOT_SUBMITTED result must carry no order state or Step ordinal"
                )
            if any(value is not None for value in identifiers):
                raise ValueError("SHADOW_NOT_SUBMITTED result must carry no identifiers")
            return
        if type(self.projected_order_state) is not PaperOrderState:
            raise TypeError("projected_order_state must be exact PaperOrderState")
        if (
            type(self.step_ordinal) is not int
            or isinstance(self.step_ordinal, bool)
            or self.step_ordinal < 0
        ):
            raise ValueError("step_ordinal must be an exact int >= 0")
        for value, label in (
            (self.paper_fill_id, "paper_fill_id"),
            (self.reservation_consumption_id, "reservation_consumption_id"),
            (self.reservation_release_id, "reservation_release_id"),
        ):
            if value is not None and (type(value) is not str or not value.strip()):
                raise ValueError(f"{label} must be None or a non-blank exact str")
        if self.disposition is PaperApplicationDisposition.PAPER_STEP_PENDING and any(
            value is not None for value in identifiers
        ):
            raise ValueError(
                "PAPER_STEP_PENDING result must carry no Fill/consumption/release identifiers"
            )


class PaperApplicationService:
    """Compose B1-B4 into exactly three one-intent Paper entry points (see spec.md "B5")."""

    def __init__(self, *, store: SQLitePaperStore, clock: Clock, worker_identity: str) -> None:
        if type(store) is not SQLitePaperStore:
            raise TypeError("store must be exact SQLitePaperStore")
        if type(worker_identity) is not str or not worker_identity.strip():
            raise ValueError("worker_identity must be a non-blank exact str")
        self._store = store
        self._clock = clock
        self._worker_identity = worker_identity

    def submit_entry_intent(
        self,
        intent: ApprovedExecutionIntent,
        *,
        authority: ExecutionAuthorityMode,
        fill_policy: PaperFillPolicy,
        account_bootstrap: PaperAccountBootstrap,
        market_observations: Sequence[PaperMarketObservation] = (),
    ) -> PaperApplicationResult:
        if type(intent) is not ApprovedExecutionIntent:
            raise TypeError("intent must be exact ApprovedExecutionIntent")
        ApprovedExecutionIntent.__post_init__(intent)
        routed = self._route_authority(authority)
        if routed is not None:
            return routed
        evaluated_at = self._read_clock()
        accepted = self._accept_or_reuse(
            lineage=PaperOrderIntentLineage.for_entry(intent),
            pair=intent.pair,
            side=intent.side,
            quantity=intent.quantity,
            intent_created_at=intent.created_at,
            fill_policy=fill_policy,
            account_bootstrap=account_bootstrap,
            evaluated_at=evaluated_at,
            accept=lambda: self._store.accept_entry_order(
                fill_policy=fill_policy,
                account_bootstrap=account_bootstrap,
                intent=intent,
                evaluated_at=evaluated_at,
            ),
        )
        return self._advance(
            accepted, market_observations=market_observations, evaluated_at=evaluated_at
        )

    def submit_ordinary_close_intent(
        self,
        intent: ApprovedCloseIntent,
        *,
        authority: ExecutionAuthorityMode,
        fill_policy: PaperFillPolicy,
        account_bootstrap: PaperAccountBootstrap,
        market_observations: Sequence[PaperMarketObservation] = (),
    ) -> PaperApplicationResult:
        if type(intent) is not ApprovedCloseIntent:
            raise TypeError("intent must be exact ApprovedCloseIntent")
        ApprovedCloseIntent.__post_init__(intent)
        if type(authority) is not ExecutionAuthorityMode:
            raise TypeError("authority must be exact ExecutionAuthorityMode")
        if intent.authority is not authority:
            raise ValueError("ApprovedCloseIntent authority does not match the supplied authority")
        routed = self._route_authority(authority)
        if routed is not None:
            return routed
        evaluated_at = self._read_clock()
        accepted = self._accept_or_reuse(
            lineage=PaperOrderIntentLineage.for_ordinary_close(intent),
            pair=intent.pair,
            side=intent.side,
            quantity=intent.quantity,
            intent_created_at=intent.created_at,
            fill_policy=fill_policy,
            account_bootstrap=account_bootstrap,
            evaluated_at=evaluated_at,
            accept=lambda: self._store.accept_ordinary_close_order(
                fill_policy=fill_policy,
                account_bootstrap=account_bootstrap,
                intent=intent,
                evaluated_at=evaluated_at,
            ),
        )
        return self._advance(
            accepted, market_observations=market_observations, evaluated_at=evaluated_at
        )

    def submit_emergency_liquidation_intent(
        self,
        intent: ApprovedLiquidationIntent,
        *,
        authority: ExecutionAuthorityMode,
        existing_position_side: Side,
        fill_policy: PaperFillPolicy,
        account_bootstrap: PaperAccountBootstrap,
        market_observations: Sequence[PaperMarketObservation] = (),
    ) -> PaperApplicationResult:
        if type(intent) is not ApprovedLiquidationIntent:
            raise TypeError("intent must be exact ApprovedLiquidationIntent")
        ApprovedLiquidationIntent.__post_init__(intent)
        routed = self._route_authority(authority)
        if routed is not None:
            return routed
        evaluated_at = self._read_clock()
        order_side = opposite_side(existing_position_side)
        accepted = self._accept_or_reuse(
            lineage=PaperOrderIntentLineage.for_emergency_liquidation(
                intent, existing_position_side=existing_position_side
            ),
            pair=intent.pair,
            side=order_side,
            quantity=intent.quantity,
            intent_created_at=intent.created_at,
            fill_policy=fill_policy,
            account_bootstrap=account_bootstrap,
            evaluated_at=evaluated_at,
            accept=lambda: self._store.accept_emergency_liquidation_order(
                fill_policy=fill_policy,
                account_bootstrap=account_bootstrap,
                intent=intent,
                existing_position_side=existing_position_side,
                evaluated_at=evaluated_at,
            ),
        )
        return self._advance(
            accepted, market_observations=market_observations, evaluated_at=evaluated_at
        )

    def _accept_or_reuse(
        self,
        *,
        lineage: PaperOrderIntentLineage,
        pair: CurrencyPair,
        side: Side,
        quantity: Decimal,
        intent_created_at: datetime,
        fill_policy: PaperFillPolicy,
        account_bootstrap: PaperAccountBootstrap,
        evaluated_at: datetime,
        accept: Callable[[], AcceptedOrder],
    ) -> AcceptedOrder:
        # Skip T1 for an intent already accepted (its paper_order_id is fully
        # content-addressed and excludes created_at, so this call's own evaluated_at
        # is a safe placeholder -- see contracts.py's PaperOrder identity_payload).
        # A caller-visible identity change (e.g. a different fill_policy) yields a
        # different paper_order_id, is treated as not-found, and T1's own
        # UNIQUE(intent_kind, source_intent_id) + _insert_or_compare machinery
        # rejects it exactly as it already rejects a first-call mismatch.
        candidate_order = PaperOrder.create(
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
        existing = self._store.hydrate_accepted_order(paper_order_id=candidate_order.paper_order_id)
        return existing if existing is not None else accept()

    def _route_authority(self, authority: object) -> PaperApplicationResult | None:
        if type(authority) is not ExecutionAuthorityMode:
            raise TypeError("authority must be exact ExecutionAuthorityMode")
        if authority is ExecutionAuthorityMode.LIVE:
            raise ValueError(
                "LIVE authority is rejected before any Paper gateway, store, clock, or market work"
            )
        if authority is ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED:
            return PaperApplicationResult.shadow_not_submitted()
        return None

    def _read_clock(self) -> datetime:
        value = self._clock.now()
        if type(value) is not datetime:
            raise TypeError("Clock.now() must return an exact datetime")
        require_utc(value, "Clock.now()")
        return value

    def _advance(
        self,
        accepted: AcceptedOrder,
        *,
        market_observations: Sequence[PaperMarketObservation],
        evaluated_at: datetime,
    ) -> PaperApplicationResult:
        observations = tuple(market_observations)
        ordinal = self._store.current_step_ordinal(plan=accepted.plan)
        existing_step = self._store.hydrate_created_step(plan=accepted.plan, ordinal=ordinal)
        created_step = (
            existing_step
            if existing_step is not None
            else self._store.create_step(
                plan=accepted.plan, ordinal=ordinal, evaluated_at=evaluated_at
            )
        )
        self._store.append_market_observations(observations)
        evaluated = self._store.evaluate_step(
            step=created_step.step,
            plan=accepted.plan,
            worker_identity=self._worker_identity,
            evaluated_at=evaluated_at,
            mark_observations=observations,
        )
        if created_step.step.ordinal == 0:
            # Ordinal 0's OPEN event is always produced or reused by T2 (see store.py
            # create_step); only ordinal > 0 calls leave it unset.
            assert created_step.open_event is not None
            projected_order_state = project_paper_order_state(
                (accepted.accepted_event, created_step.open_event, *evaluated.order_events)
            )
        else:
            # A later Step is only reached after a preceding positive-Fill continuation
            # (spec.md "The next Step is created only when..."), so a still-PENDING
            # outcome here is necessarily PARTIALLY_FILLED; a resolved outcome's own
            # last order event (fresh or replayed, see store.py's evaluate_step) is
            # authoritative and needs no combining with earlier calls' events.
            projected_order_state = (
                evaluated.order_events[-1].state
                if evaluated.order_events
                else PaperOrderState.PARTIALLY_FILLED
            )
        if evaluated.outcome is StepResolutionOutcome.PENDING:
            return PaperApplicationResult.pending(
                projected_order_state=projected_order_state,
                step_ordinal=created_step.step.ordinal,
            )
        return PaperApplicationResult.resolved(
            projected_order_state=projected_order_state,
            step_ordinal=created_step.step.ordinal,
            paper_fill_id=None if evaluated.fill is None else evaluated.fill.paper_fill_id,
            reservation_consumption_id=(
                None
                if evaluated.reservation_consumption is None
                else evaluated.reservation_consumption.consumption_id
            ),
            reservation_release_id=(
                None
                if evaluated.reservation_release is None
                else evaluated.reservation_release.release_id
            ),
        )
