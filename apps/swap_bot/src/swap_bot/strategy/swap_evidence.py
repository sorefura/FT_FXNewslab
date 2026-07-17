from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from fx_core import Currency, CurrencyPair
from fx_core.time import require_utc

from ..adoption import digest
from ..swap import SwapAvailability

OPERATIONAL_SWAP_EVIDENCE_VERSION = "operational-swap-evidence-v1"


@dataclass(frozen=True, slots=True)
class OperationalSwapEvidence:
    swap_evidence_id: str
    evidence_contract_version: str
    pair: CurrencyPair
    availability: SwapAvailability
    long_received_amount: Decimal | None
    short_received_amount: Decimal | None
    unit_basis: str | None
    settlement_currency: Currency | None
    source: str
    source_version: str
    provider_observed_at: datetime
    received_at: datetime
    effective_from: datetime
    effective_until: datetime | None

    def __post_init__(self) -> None:
        self.validate_intrinsic_integrity()

    @classmethod
    def create(
        cls,
        *,
        evidence_contract_version: str,
        pair: CurrencyPair,
        availability: SwapAvailability,
        long_received_amount: Decimal | None,
        short_received_amount: Decimal | None,
        unit_basis: str | None,
        settlement_currency: Currency | None,
        source: str,
        source_version: str,
        provider_observed_at: datetime,
        received_at: datetime,
        effective_from: datetime,
        effective_until: datetime | None,
    ) -> "OperationalSwapEvidence":
        payload = _identity_payload(
            evidence_contract_version=evidence_contract_version,
            pair=pair,
            availability=availability,
            long_received_amount=long_received_amount,
            short_received_amount=short_received_amount,
            unit_basis=unit_basis,
            settlement_currency=settlement_currency,
            source=source,
            source_version=source_version,
            provider_observed_at=provider_observed_at,
            received_at=received_at,
            effective_from=effective_from,
            effective_until=effective_until,
        )
        return cls(
            swap_evidence_id="swap-evidence-" + digest(payload),
            evidence_contract_version=evidence_contract_version,
            pair=pair,
            availability=availability,
            long_received_amount=long_received_amount,
            short_received_amount=short_received_amount,
            unit_basis=unit_basis,
            settlement_currency=settlement_currency,
            source=source,
            source_version=source_version,
            provider_observed_at=provider_observed_at,
            received_at=received_at,
            effective_from=effective_from,
            effective_until=effective_until,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _identity_payload(
            evidence_contract_version=self.evidence_contract_version,
            pair=self.pair,
            availability=self.availability,
            long_received_amount=self.long_received_amount,
            short_received_amount=self.short_received_amount,
            unit_basis=self.unit_basis,
            settlement_currency=self.settlement_currency,
            source=self.source,
            source_version=self.source_version,
            provider_observed_at=self.provider_observed_at,
            received_at=self.received_at,
            effective_from=self.effective_from,
            effective_until=self.effective_until,
        )

    @property
    def expected_swap_evidence_id(self) -> str:
        return "swap-evidence-" + digest(self.identity_payload)

    def validate_intrinsic_integrity(self) -> None:
        if self.evidence_contract_version != OPERATIONAL_SWAP_EVIDENCE_VERSION:
            raise ValueError("unsupported OperationalSwapEvidence contract")
        if not self.source.strip() or not self.source_version.strip():
            raise ValueError("swap source and source_version are required")
        require_utc(self.provider_observed_at, "swap evidence provider_observed_at")
        require_utc(self.received_at, "swap evidence received_at")
        require_utc(self.effective_from, "swap evidence effective_from")
        if self.effective_until is not None:
            require_utc(self.effective_until, "swap evidence effective_until")
        if self.provider_observed_at > self.received_at:
            raise ValueError("provider_observed_at cannot be after received_at")
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError("effective_until must be after effective_from")
        amounts = (self.long_received_amount, self.short_received_amount)
        if self.availability is SwapAvailability.AVAILABLE:
            if any(amount is None for amount in amounts):
                raise ValueError("available swap evidence requires long and short amounts")
            if self.unit_basis is None or not self.unit_basis.strip():
                raise ValueError("available swap evidence requires unit_basis")
            if self.settlement_currency is None:
                raise ValueError("available swap evidence requires settlement_currency")
        elif self.availability in {
            SwapAvailability.UNKNOWN,
            SwapAvailability.UNAVAILABLE,
            SwapAvailability.NOT_APPLICABLE,
        } and any(amount is not None for amount in amounts):
            raise ValueError("non-numeric swap availability cannot contain amounts")
        elif self.availability is SwapAvailability.STALE:
            if (self.long_received_amount is None) != (self.short_received_amount is None):
                raise ValueError("stale numeric evidence requires both amounts")
            if self.long_received_amount is not None and (
                self.unit_basis is None
                or not self.unit_basis.strip()
                or self.settlement_currency is None
            ):
                raise ValueError("stale numeric evidence requires unit and currency")
        if self.swap_evidence_id != self.expected_swap_evidence_id:
            raise ValueError("swap_evidence_id does not match intrinsic evidence")


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _identity_payload(
    *,
    evidence_contract_version: str,
    pair: CurrencyPair,
    availability: SwapAvailability,
    long_received_amount: Decimal | None,
    short_received_amount: Decimal | None,
    unit_basis: str | None,
    settlement_currency: Currency | None,
    source: str,
    source_version: str,
    provider_observed_at: datetime,
    received_at: datetime,
    effective_from: datetime,
    effective_until: datetime | None,
) -> dict[str, object]:
    return {
        "evidence_contract_version": evidence_contract_version,
        "pair": pair.symbol,
        "availability": availability.value,
        "long_received_amount": _decimal(long_received_amount),
        "short_received_amount": _decimal(short_received_amount),
        "unit_basis": unit_basis,
        "settlement_currency": (
            None if settlement_currency is None else settlement_currency.code
        ),
        "source": source,
        "source_version": source_version,
        "provider_observed_at": provider_observed_at.isoformat(),
        "received_at": received_at.isoformat(),
        "effective_from": effective_from.isoformat(),
        "effective_until": (
            None if effective_until is None else effective_until.isoformat()
        ),
    }
