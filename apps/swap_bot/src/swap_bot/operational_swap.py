from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

from fx_core import Currency, CurrencyPair
from fx_core.time import require_utc

from .adoption import digest
from .live_migrations import migrate_live_database
from .strategy import OperationalSwapEvidence
from .swap import SwapAvailability


class OperationalSwapPersistenceConflict(ValueError):
    pass


class OperationalSwapIntegrityError(RuntimeError):
    pass


class OperationalSwapAppendDisposition(StrEnum):
    INSERTED = "INSERTED"
    REUSED_IDENTICAL = "REUSED_IDENTICAL"


class OperationalSwapReadDisposition(StrEnum):
    FOUND = "FOUND"
    MISSING = "MISSING"


class OperationalSwapResolutionOutcome(StrEnum):
    EVIDENCE = "EVIDENCE"
    MISSING = "MISSING"
    MALFORMED = "MALFORMED"


@dataclass(frozen=True, slots=True)
class OperationalSwapResolution:
    resolution_id: str
    pair: CurrencyPair
    source: str
    source_version: str
    requested_at: datetime
    outcome: OperationalSwapResolutionOutcome
    reason_code: str
    evidence: OperationalSwapEvidence | None

    def __post_init__(self) -> None:
        self.validate_intrinsic_integrity()

    @classmethod
    def create(
        cls,
        *,
        pair: CurrencyPair,
        source: str,
        source_version: str,
        requested_at: datetime,
        outcome: OperationalSwapResolutionOutcome,
        reason_code: str,
        evidence: OperationalSwapEvidence | None,
    ) -> OperationalSwapResolution:
        payload = _resolution_payload(
            pair=pair,
            source=source,
            source_version=source_version,
            requested_at=requested_at,
            outcome=outcome,
            reason_code=reason_code,
            evidence=evidence,
        )
        return cls(
            resolution_id="swap-resolution-" + digest(payload),
            pair=pair,
            source=source,
            source_version=source_version,
            requested_at=requested_at,
            outcome=outcome,
            reason_code=reason_code,
            evidence=evidence,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _resolution_payload(
            pair=self.pair,
            source=self.source,
            source_version=self.source_version,
            requested_at=self.requested_at,
            outcome=self.outcome,
            reason_code=self.reason_code,
            evidence=self.evidence,
        )

    def validate_intrinsic_integrity(self) -> None:
        if type(self.resolution_id) is not str:
            raise TypeError("resolution_id must be exact str")
        if type(self.pair) is not CurrencyPair:
            raise TypeError("resolution pair must be exact CurrencyPair")
        if type(self.pair.base) is not Currency or type(self.pair.quote) is not Currency:
            raise TypeError("resolution Pair currencies must be exact Currency")
        Currency.__post_init__(self.pair.base)
        Currency.__post_init__(self.pair.quote)
        CurrencyPair.__post_init__(self.pair)
        if type(self.source) is not str or type(self.source_version) is not str:
            raise TypeError("resolution source values must be exact str")
        if type(self.reason_code) is not str:
            raise TypeError("resolution reason_code must be exact str")
        if (
            not self.source.strip()
            or not self.source_version.strip()
            or not self.reason_code.strip()
        ):
            raise ValueError("resolution source, version, and reason code are required")
        if type(self.requested_at) is not datetime:
            raise TypeError("resolution requested_at must be exact datetime")
        require_utc(self.requested_at, "Swap resolution requested_at")
        if type(self.outcome) is not OperationalSwapResolutionOutcome:
            raise TypeError("resolution outcome must be exact OperationalSwapResolutionOutcome")
        if self.outcome is OperationalSwapResolutionOutcome.EVIDENCE:
            if type(self.evidence) is not OperationalSwapEvidence:
                raise TypeError("EVIDENCE resolution requires exact OperationalSwapEvidence")
            OperationalSwapEvidence.validate_intrinsic_integrity(self.evidence)
            if self.evidence.pair != self.pair:
                raise ValueError("Swap resolution Evidence belongs to another Pair")
            if (
                self.evidence.source != self.source
                or self.evidence.source_version != self.source_version
            ):
                raise ValueError("Swap resolution source differs from its Evidence")
        elif self.evidence is not None:
            raise ValueError("MISSING and MALFORMED resolutions cannot contain Evidence")
        if self.resolution_id != "swap-resolution-" + digest(self.identity_payload):
            raise ValueError("resolution_id does not match intrinsic content")


@dataclass(frozen=True, slots=True)
class OperationalSwapAppendResult:
    disposition: OperationalSwapAppendDisposition
    evidence: OperationalSwapEvidence

    def __post_init__(self) -> None:
        if type(self.disposition) is not OperationalSwapAppendDisposition:
            raise TypeError("append disposition must be exact OperationalSwapAppendDisposition")
        if type(self.evidence) is not OperationalSwapEvidence:
            raise TypeError("append result requires exact OperationalSwapEvidence")
        OperationalSwapEvidence.validate_intrinsic_integrity(self.evidence)


@dataclass(frozen=True, slots=True)
class OperationalSwapReadResult:
    disposition: OperationalSwapReadDisposition
    evidence: OperationalSwapEvidence | None

    def __post_init__(self) -> None:
        if type(self.disposition) is not OperationalSwapReadDisposition:
            raise TypeError("read disposition must be exact OperationalSwapReadDisposition")
        if self.disposition is OperationalSwapReadDisposition.FOUND:
            if type(self.evidence) is not OperationalSwapEvidence:
                raise TypeError("FOUND requires exact OperationalSwapEvidence")
            OperationalSwapEvidence.validate_intrinsic_integrity(self.evidence)
        elif self.evidence is not None:
            raise ValueError("MISSING cannot contain OperationalSwapEvidence")


class SQLiteOperationalSwapStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            migrate_live_database(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def append_or_compare(
        self, evidence: OperationalSwapEvidence
    ) -> OperationalSwapAppendResult:
        with closing(self._connect()) as connection, connection:
            return self.append_or_compare_on(connection, evidence)

    @staticmethod
    def append_or_compare_on(
        connection: sqlite3.Connection, evidence: OperationalSwapEvidence
    ) -> OperationalSwapAppendResult:
        if type(evidence) is not OperationalSwapEvidence:
            raise TypeError("evidence must be exact OperationalSwapEvidence")
        OperationalSwapEvidence.validate_intrinsic_integrity(evidence)
        values = _values(evidence)
        cursor = connection.execute(
            "INSERT OR IGNORE INTO live_operational_swap_evidence VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        row = connection.execute(
            "SELECT * FROM live_operational_swap_evidence WHERE swap_evidence_id = ?",
            (evidence.swap_evidence_id,),
        ).fetchone()
        if row is None:
            raise OperationalSwapIntegrityError("Operational Swap evidence was not persisted")
        persisted = _hydrate(row)
        if persisted != evidence:
            raise OperationalSwapPersistenceConflict(
                "Operational Swap evidence ID already has different content"
            )
        disposition = (
            OperationalSwapAppendDisposition.INSERTED
            if cursor.rowcount == 1
            else OperationalSwapAppendDisposition.REUSED_IDENTICAL
        )
        return OperationalSwapAppendResult(disposition, persisted)

    def get_exact(self, swap_evidence_id: str) -> OperationalSwapReadResult:
        if type(swap_evidence_id) is not str:
            raise TypeError("swap_evidence_id must be exact str")
        if not swap_evidence_id.strip():
            raise ValueError("swap_evidence_id must be nonblank")
        with closing(self._connect()) as connection:
            return self.get_exact_on(connection, swap_evidence_id)

    @staticmethod
    def get_exact_on(
        connection: sqlite3.Connection, swap_evidence_id: str
    ) -> OperationalSwapReadResult:
        row = connection.execute(
            "SELECT * FROM live_operational_swap_evidence WHERE swap_evidence_id = ?",
            (swap_evidence_id,),
        ).fetchone()
        if row is None:
            return OperationalSwapReadResult(OperationalSwapReadDisposition.MISSING, None)
        return OperationalSwapReadResult(OperationalSwapReadDisposition.FOUND, _hydrate(row))

    append = append_or_compare
    get = get_exact


def _values(evidence: OperationalSwapEvidence) -> tuple[str | None, ...]:
    return (
        evidence.swap_evidence_id,
        evidence.evidence_contract_version,
        evidence.pair.symbol,
        evidence.availability.value,
        _decimal_text(evidence.long_received_amount),
        _decimal_text(evidence.short_received_amount),
        evidence.unit_basis,
        None if evidence.settlement_currency is None else evidence.settlement_currency.code,
        evidence.source,
        evidence.source_version,
        evidence.provider_observed_at.isoformat(),
        evidence.received_at.isoformat(),
        evidence.effective_from.isoformat(),
        None if evidence.effective_until is None else evidence.effective_until.isoformat(),
    )


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _hydrate(row: sqlite3.Row) -> OperationalSwapEvidence:
    try:
        return OperationalSwapEvidence(
            swap_evidence_id=row["swap_evidence_id"],
            evidence_contract_version=row["evidence_contract_version"],
            pair=CurrencyPair.parse(row["pair"]),
            availability=SwapAvailability(row["availability"]),
            long_received_amount=_decimal_from_text(row["long_received_amount"]),
            short_received_amount=_decimal_from_text(row["short_received_amount"]),
            unit_basis=row["unit_basis"],
            settlement_currency=(
                None if row["settlement_currency"] is None else Currency(row["settlement_currency"])
            ),
            source=row["source"],
            source_version=row["source_version"],
            provider_observed_at=datetime.fromisoformat(row["provider_observed_at"]),
            received_at=datetime.fromisoformat(row["received_at"]),
            effective_from=datetime.fromisoformat(row["effective_from"]),
            effective_until=(
                None
                if row["effective_until"] is None
                else datetime.fromisoformat(row["effective_until"])
            ),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as error:
        raise OperationalSwapIntegrityError(
            "persisted Operational Swap evidence is malformed"
        ) from error


def _decimal_from_text(value: str | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(value)


def _resolution_payload(
    *,
    pair: CurrencyPair,
    source: str,
    source_version: str,
    requested_at: datetime,
    outcome: OperationalSwapResolutionOutcome,
    reason_code: str,
    evidence: OperationalSwapEvidence | None,
) -> dict[str, object]:
    return {
        "pair": pair.symbol,
        "source": source,
        "source_version": source_version,
        "requested_at": requested_at.isoformat(),
        "outcome": outcome.value,
        "reason_code": reason_code,
        "evidence_id": None if evidence is None else evidence.swap_evidence_id,
        "evidence": None if evidence is None else evidence.identity_payload,
    }
