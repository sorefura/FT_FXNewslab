from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fx_core import Currency
from swap_bot.strategy import OperationalSwapEvidence
from swap_bot.swap import SwapAvailability

from tests.strategy_contracts.factories import NOW, swap_evidence


def test_same_operational_swap_evidence_has_same_content_id() -> None:
    first = swap_evidence()
    second = swap_evidence()

    assert first.swap_evidence_id == second.swap_evidence_id
    assert first.expected_swap_evidence_id == first.swap_evidence_id


def test_received_time_and_content_change_swap_evidence_identity() -> None:
    baseline = swap_evidence()

    assert swap_evidence(received_at=NOW).swap_evidence_id != baseline.swap_evidence_id
    assert (
        swap_evidence(long_received_amount=Decimal("12.51")).swap_evidence_id
        != baseline.swap_evidence_id
    )


def test_forged_swap_evidence_id_is_rejected_and_evidence_is_immutable() -> None:
    evidence = swap_evidence()
    with pytest.raises(ValueError, match="does not match"):
        replace(evidence, swap_evidence_id="swap-evidence-forged")
    with pytest.raises(FrozenInstanceError):
        evidence.source_version = "changed"  # type: ignore[misc]


def test_unsupported_contract_and_non_utc_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        swap_evidence(evidence_contract_version="operational-swap-evidence-v2")
    with pytest.raises(ValueError, match="timezone-aware"):
        swap_evidence(received_at=datetime(2026, 7, 17, 3, 0))
    with pytest.raises(ValueError, match="must be UTC"):
        swap_evidence(received_at=datetime(2026, 7, 17, 12, 0, tzinfo=timezone(timedelta(hours=9))))


def test_provider_observation_cannot_postdate_local_receipt() -> None:
    with pytest.raises(ValueError, match="cannot be after"):
        swap_evidence(provider_observed_at=NOW, received_at=NOW - timedelta(seconds=1))


@pytest.mark.parametrize(
    "changes",
    [
        {"long_received_amount": None},
        {"short_received_amount": None},
        {"unit_basis": None},
        {"settlement_currency": None},
    ],
)
def test_available_swap_requires_both_amounts_unit_and_currency(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        swap_evidence(**changes)


@pytest.mark.parametrize(
    "availability",
    [
        SwapAvailability.UNKNOWN,
        SwapAvailability.UNAVAILABLE,
        SwapAvailability.NOT_APPLICABLE,
    ],
)
def test_non_numeric_availability_cannot_fabricate_zero_or_amounts(
    availability: SwapAvailability,
) -> None:
    with pytest.raises(ValueError, match="cannot contain amounts"):
        swap_evidence(availability=availability)


def test_stale_evidence_can_preserve_original_signed_amounts() -> None:
    stale = swap_evidence(availability=SwapAvailability.STALE)

    assert stale.long_received_amount == Decimal("12.50")
    assert stale.short_received_amount == Decimal("-15.25")


@pytest.mark.parametrize("field", ["long_received_amount", "short_received_amount"])
@pytest.mark.parametrize(
    "non_finite",
    [
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
def test_available_swap_rejects_each_non_finite_decimal_amount(
    field: str, non_finite: Decimal
) -> None:
    with pytest.raises(ValueError, match="finite Decimal"):
        swap_evidence(**{field: non_finite})


@pytest.mark.parametrize(
    "changes",
    [
        {"long_received_amount": Decimal("NaN")},
        {"short_received_amount": Decimal("Infinity")},
        {
            "long_received_amount": Decimal("sNaN"),
            "short_received_amount": Decimal("-Infinity"),
        },
    ],
)
def test_numeric_stale_swap_rejects_non_finite_decimal_amounts(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="finite Decimal"):
        swap_evidence(availability=SwapAvailability.STALE, **changes)


@pytest.mark.parametrize(
    ("long_amount", "short_amount"),
    [
        (Decimal("1.25"), Decimal("2.50")),
        (Decimal("-1.25"), Decimal("-2.50")),
        (Decimal("0"), Decimal("0")),
        (Decimal("-0"), Decimal("-0")),
    ],
)
def test_finite_positive_negative_and_signed_zero_remain_exact_evidence(
    long_amount: Decimal, short_amount: Decimal
) -> None:
    evidence = swap_evidence(
        long_received_amount=long_amount,
        short_received_amount=short_amount,
    )

    assert evidence.long_received_amount == long_amount
    assert evidence.short_received_amount == short_amount
    assert evidence.identity_payload["long_received_amount"] == str(long_amount)
    assert evidence.identity_payload["short_received_amount"] == str(short_amount)
    assert evidence.swap_evidence_id == evidence.expected_swap_evidence_id


def test_swap_received_sign_semantics_are_preserved_without_provider_reversal() -> None:
    evidence = swap_evidence(
        long_received_amount=Decimal("3.25"),
        short_received_amount=Decimal("-4.75"),
        settlement_currency=Currency("JPY"),
    )

    assert evidence.long_received_amount == Decimal("3.25")
    assert evidence.short_received_amount == Decimal("-4.75")


def test_effective_window_must_move_forward() -> None:
    with pytest.raises(ValueError, match="must be after"):
        swap_evidence(effective_from=NOW, effective_until=NOW)


def test_direct_constructor_still_validates_external_identity() -> None:
    evidence = swap_evidence()
    values = {field: getattr(evidence, field) for field in evidence.__dataclass_fields__}
    values["swap_evidence_id"] = "external-id"

    with pytest.raises(ValueError, match="does not match"):
        OperationalSwapEvidence(**values)


def test_utc_constant_is_utc() -> None:
    assert NOW.utcoffset() == UTC.utcoffset(NOW)
