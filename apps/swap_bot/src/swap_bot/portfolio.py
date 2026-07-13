from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from fx_core import Currency

from .models import (
    CurrencyExposure,
    CurrencyExposureSnapshot,
    PendingIntent,
    PortfolioDecision,
    PortfolioDecisionId,
    PortfolioDisposition,
    PositionSnapshot,
    TradeCandidate,
)


class CurrencyExposureCalculator:
    def calculate(
        self,
        positions: Sequence[PositionSnapshot],
        pending_intents: Sequence[PendingIntent],
        *,
        created_at: datetime,
    ) -> CurrencyExposureSnapshot:
        totals: dict[Currency, Decimal] = {}
        for position in positions:
            self._add_pair_exposure(
                totals,
                base=position.pair.base,
                quote=position.pair.quote,
                side_sign=position.side.sign,
                quantity=position.quantity,
                price=position.current_price,
            )
        for intent in pending_intents:
            self._add_pair_exposure(
                totals,
                base=intent.pair.base,
                quote=intent.pair.quote,
                side_sign=intent.side.sign,
                quantity=intent.quantity,
                price=intent.reference_price,
            )
        return CurrencyExposureSnapshot(
            exposures=tuple(
                CurrencyExposure(currency, amount)
                for currency, amount in sorted(totals.items(), key=lambda item: item[0].code)
            ),
            created_at=created_at,
        )

    @staticmethod
    def _add_pair_exposure(
        totals: dict[Currency, Decimal],
        *,
        base: Currency,
        quote: Currency,
        side_sign: Decimal,
        quantity: Decimal,
        price: Decimal,
    ) -> None:
        totals[base] = totals.get(base, Decimal(0)) + side_sign * quantity
        totals[quote] = totals.get(quote, Decimal(0)) - side_sign * quantity * price


class PortfolioService:
    def __init__(
        self,
        exposure_limits: Mapping[Currency, Decimal],
        calculator: CurrencyExposureCalculator | None = None,
    ) -> None:
        self._limits = dict(exposure_limits)
        if any(limit <= 0 for limit in self._limits.values()):
            raise ValueError("Currency exposure limits must be positive")
        self._calculator = calculator or CurrencyExposureCalculator()

    def evaluate(
        self,
        candidate: TradeCandidate,
        *,
        positions: Sequence[PositionSnapshot],
        pending_intents: Sequence[PendingIntent],
        requested_quantity: Decimal,
        reference_price: Decimal,
        decision_id: PortfolioDecisionId,
        created_at: datetime,
    ) -> PortfolioDecision:
        if requested_quantity <= 0 or reference_price <= 0:
            raise ValueError("Requested quantity and reference price must be positive")
        current = self._calculator.calculate(
            positions, pending_intents, created_at=created_at
        )
        proposed = PendingIntent(
            pair=candidate.pair,
            side=candidate.side,
            quantity=requested_quantity,
            reference_price=reference_price,
        )
        full = self._calculator.calculate(
            positions, (*pending_intents, proposed), created_at=created_at
        )
        scale = Decimal(1)
        for currency in (candidate.pair.base, candidate.pair.quote):
            limit = self._limits.get(currency)
            if limit is None:
                continue
            before = current.amount_for(currency)
            after = full.amount_for(currency)
            contribution = after - before
            if abs(after) <= limit or abs(after) <= abs(before):
                continue
            headroom = max(Decimal(0), limit - abs(before))
            scale = min(scale, headroom / abs(contribution))
        if scale <= 0:
            return PortfolioDecision(
                decision_id=decision_id,
                candidate_id=candidate.candidate_id,
                disposition=PortfolioDisposition.REJECT,
                proposed_quantity=None,
                reason_code="max_currency_exposure",
                exposure_snapshot=full,
                created_at=created_at,
            )
        if scale < 1:
            reduced = PendingIntent(
                pair=candidate.pair,
                side=candidate.side,
                quantity=requested_quantity * scale,
                reference_price=reference_price,
            )
            reduced_exposure = self._calculator.calculate(
                positions, (*pending_intents, reduced), created_at=created_at
            )
            return PortfolioDecision(
                decision_id=decision_id,
                candidate_id=candidate.candidate_id,
                disposition=PortfolioDisposition.REDUCE,
                proposed_quantity=requested_quantity * scale,
                reason_code="reduced_for_currency_exposure",
                exposure_snapshot=reduced_exposure,
                created_at=created_at,
            )
        return PortfolioDecision(
            decision_id=decision_id,
            candidate_id=candidate.candidate_id,
            disposition=PortfolioDisposition.ACCEPT,
            proposed_quantity=requested_quantity,
            reason_code="within_exposure_limits",
            exposure_snapshot=full,
            created_at=created_at,
        )
