import sqlite3
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from pathlib import Path

import pytest
from fx_core import Currency, CurrencyTarget, Horizon, VersionMetadata
from swap_bot.adoption import (
    AdoptionFailureReason,
    AdoptionRejected,
    ResearchValidationEvidenceSnapshot,
    RuntimeMode,
    approval_decision,
    revocation_decision,
)
from swap_bot.adoption_application import ApproveSignalAdoptionOnceService
from swap_bot.adoption_gate import LiveAdoptionGate
from swap_bot.adoption_store import SQLiteAdoptionStore
from swap_bot.research_evidence import SQLiteResearchValidationEvidenceSource

from tests.adoption_factories import (
    NOW,
    adoptable_signal,
    adoption_policy,
    seed_research_evidence,
)


def _approved_store(
    tmp_path: Path, *, policy=None, approval_time=NOW  # type: ignore[no-untyped-def]
) -> tuple[SQLiteAdoptionStore, str, Path]:
    research = tmp_path / "research.sqlite3"
    live = tmp_path / "live.sqlite3"
    seed_research_evidence(research)
    store = SQLiteAdoptionStore(live)
    result = ApproveSignalAdoptionOnceService(
        SQLiteResearchValidationEvidenceSource(research), clock=lambda: approval_time
    ).run(
        assessment_id="assessment-validated-1",
        policy=policy or adoption_policy(),
        approved_by="reviewer@example.com",
        reason="reviewed evidence",
        apply=True,
        store=store,
    )
    return store, result.adoption_decision_id, research


def test_exact_fresh_signal_is_authorized_without_runtime_research_read(
    tmp_path: Path,
) -> None:
    store, _, research = _approved_store(tmp_path)
    signal = adoptable_signal()

    with sqlite3.connect(research) as research_lock:
        research_lock.execute("BEGIN EXCLUSIVE")
        authorized = LiveAdoptionGate(store).authorize(
            signal,
            strategy_id="validated-signal-shadow",
            strategy_version="strategy-v1",
            runtime_mode=RuntimeMode.SHADOW,
            authorized_at=NOW,
        )

    assert authorized.signal is signal
    assert authorized.authorization.signal_id == signal.signal_id.value
    assert store.count_rows("live_signal_authorizations") == 1


def test_validated_but_not_adopted_signal_fails_before_strategy(tmp_path: Path) -> None:
    store = SQLiteAdoptionStore(tmp_path / "live.sqlite3")

    with pytest.raises(AdoptionRejected) as rejected:
        LiveAdoptionGate(store).authorize(
            adoptable_signal(),
            strategy_id="validated-signal-shadow",
            strategy_version="strategy-v1",
            runtime_mode=RuntimeMode.SHADOW,
            authorized_at=NOW,
        )

    assert rejected.value.reason is AdoptionFailureReason.NO_ACTIVE_ADOPTION


def test_signal_created_before_effective_time_is_not_retroactively_activated(
    tmp_path: Path,
) -> None:
    policy = adoption_policy(effective_from=NOW - timedelta(minutes=1))
    store, _, _ = _approved_store(tmp_path, policy=policy)
    historical = adoptable_signal(created_at=NOW - timedelta(minutes=2))

    with pytest.raises(AdoptionRejected) as rejected:
        LiveAdoptionGate(store).authorize(
            historical,
            strategy_id=policy.strategy_id,
            strategy_version=policy.strategy_version,
            runtime_mode=RuntimeMode.SHADOW,
            authorized_at=NOW,
        )

    assert rejected.value.reason is AdoptionFailureReason.ADOPTION_NOT_YET_EFFECTIVE


def test_not_yet_effective_and_expired_approvals_fail_closed(tmp_path: Path) -> None:
    future = adoption_policy(
        effective_from=NOW + timedelta(hours=1),
        expires_at=NOW + timedelta(days=1),
    )
    future_store, _, _ = _approved_store(tmp_path / "future", policy=future)
    with pytest.raises(AdoptionRejected) as not_yet:
        LiveAdoptionGate(future_store).authorize(
            adoptable_signal(),
            strategy_id=future.strategy_id,
            strategy_version=future.strategy_version,
            runtime_mode=RuntimeMode.SHADOW,
            authorized_at=NOW,
        )
    expired = adoption_policy(
        effective_from=NOW - timedelta(days=3),
        expires_at=NOW - timedelta(days=1),
    )
    expired_store, _, _ = _approved_store(
        tmp_path / "expired",
        policy=expired,
        approval_time=NOW - timedelta(days=2),
    )
    with pytest.raises(AdoptionRejected) as past:
        LiveAdoptionGate(expired_store).authorize(
            adoptable_signal(created_at=NOW - timedelta(days=2)),
            strategy_id=expired.strategy_id,
            strategy_version=expired.strategy_version,
            runtime_mode=RuntimeMode.SHADOW,
            authorized_at=NOW,
        )

    assert not_yet.value.reason is AdoptionFailureReason.ADOPTION_NOT_YET_EFFECTIVE
    assert past.value.reason is AdoptionFailureReason.ADOPTION_EXPIRED


def test_revocation_prevents_new_authorization(tmp_path: Path) -> None:
    store, approval_id, _ = _approved_store(tmp_path)
    approval = store.get_decision(approval_id)
    store.append_revocation(
        revocation_decision(
            approval,
            decided_at=NOW,
            actor="reviewer@example.com",
            reason="superseded",
        )
    )

    with pytest.raises(AdoptionRejected) as rejected:
        LiveAdoptionGate(store).authorize(
            adoptable_signal(),
            strategy_id=approval.strategy_id,
            strategy_version=approval.strategy_version,
            runtime_mode=RuntimeMode.SHADOW,
            authorized_at=NOW + timedelta(seconds=1),
        )

    assert rejected.value.reason is AdoptionFailureReason.ADOPTION_REVOKED


def test_shadow_only_approval_is_not_live_eligible(tmp_path: Path) -> None:
    store, _, _ = _approved_store(tmp_path)

    with pytest.raises(AdoptionRejected) as rejected:
        LiveAdoptionGate(store).authorize(
            adoptable_signal(),
            strategy_id="validated-signal-shadow",
            strategy_version="strategy-v1",
            runtime_mode=RuntimeMode.LIVE,
            authorized_at=NOW,
        )

    assert rejected.value.reason is AdoptionFailureReason.ADOPTION_MODE_NOT_ALLOWED


@pytest.mark.parametrize(
    "changed_signal",
    [
        replace(
            adoptable_signal(),
            versions=VersionMetadata(
                producer_version="producer-v1",
                model_version="model-v2",
                prompt_version="prompt-v1",
                scorer_version="fundamental-scorer-v1",
            ),
        ),
        replace(adoptable_signal(), target=CurrencyTarget(Currency("JPY"))),
        replace(adoptable_signal(), horizon=Horizon.DAY_1),
    ],
)
def test_signal_semantic_mismatch_is_rejected(tmp_path: Path, changed_signal) -> None:  # type: ignore[no-untyped-def]
    store, _, _ = _approved_store(tmp_path)

    with pytest.raises(AdoptionRejected) as rejected:
        LiveAdoptionGate(store).authorize(
            changed_signal,
            strategy_id="validated-signal-shadow",
            strategy_version="strategy-v1",
            runtime_mode=RuntimeMode.SHADOW,
            authorized_at=NOW,
        )

    assert rejected.value.reason is AdoptionFailureReason.SIGNAL_SPECIFICATION_MISMATCH


def test_multiple_active_exact_approvals_are_ambiguous(tmp_path: Path) -> None:
    store, _, research = _approved_store(tmp_path)
    evidence = SQLiteResearchValidationEvidenceSource(research).read(
        "assessment-validated-1"
    )
    snapshot = ResearchValidationEvidenceSnapshot.from_evidence(evidence, imported_at=NOW)
    second_policy = adoption_policy(adoption_policy_version="adoption-policy-v2")
    store.apply_approval(
        snapshot,
        second_policy,
        approval_decision(
            snapshot,
            second_policy,
            decided_at=NOW,
            actor="reviewer@example.com",
            reason="parallel approval",
        ),
    )

    with pytest.raises(AdoptionRejected) as rejected:
        LiveAdoptionGate(store).authorize(
            adoptable_signal(),
            strategy_id=second_policy.strategy_id,
            strategy_version=second_policy.strategy_version,
            runtime_mode=RuntimeMode.SHADOW,
            authorized_at=NOW,
        )

    assert rejected.value.reason is AdoptionFailureReason.AMBIGUOUS_ADOPTION


def test_authorization_is_idempotent_and_signal_remains_immutable(tmp_path: Path) -> None:
    store, _, _ = _approved_store(tmp_path)
    signal = adoptable_signal()
    gate = LiveAdoptionGate(store)

    first = gate.authorize(
        signal,
        strategy_id="validated-signal-shadow",
        strategy_version="strategy-v1",
        runtime_mode=RuntimeMode.SHADOW,
        authorized_at=NOW,
    )
    second = gate.authorize(
        signal,
        strategy_id="validated-signal-shadow",
        strategy_version="strategy-v1",
        runtime_mode=RuntimeMode.SHADOW,
        authorized_at=NOW + timedelta(seconds=1),
    )

    assert first.authorization.authorization_id == second.authorization.authorization_id
    assert store.count_rows("live_signal_authorizations") == 1
    with pytest.raises(FrozenInstanceError):
        signal.signal_type = "changed"  # type: ignore[misc]
