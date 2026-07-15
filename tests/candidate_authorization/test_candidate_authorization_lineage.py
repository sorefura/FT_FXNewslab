from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fx_core import CurrencyPair, Probability
from swap_bot.adoption import (
    AdoptionFailureReason,
    AdoptionRejected,
    RuntimeMode,
    revocation_decision,
)
from swap_bot.adoption_application import ApproveSignalAdoptionOnceService
from swap_bot.adoption_gate import LiveAdoptionGate
from swap_bot.adoption_store import SQLiteAdoptionStore
from swap_bot.decision_store import SQLiteLiveDecisionStore
from swap_bot.models import (
    CandidateId,
    CurrencyExposureSnapshot,
    PortfolioDecision,
    PortfolioDecisionId,
    PortfolioDisposition,
    RiskDecision,
    RiskDecisionId,
    RiskDisposition,
    Side,
    TradeCandidate,
)
from swap_bot.research_evidence import SQLiteResearchValidationEvidenceSource

from tests.adoption_factories import (
    NOW,
    adoptable_signal,
    adoption_policy,
    seed_research_evidence,
)


def _authorized(
    tmp_path: Path, *, signal_id: str = "signal-adopted-1"
):  # type: ignore[no-untyped-def]
    research = tmp_path / "research.sqlite3"
    live = tmp_path / "live.sqlite3"
    seed_research_evidence(research)
    adoption_store = SQLiteAdoptionStore(live)
    approval = ApproveSignalAdoptionOnceService(
        SQLiteResearchValidationEvidenceSource(research), clock=lambda: NOW
    ).run(
        assessment_id="assessment-validated-1",
        policy=adoption_policy(),
        approved_by="reviewer@example.com",
        reason="reviewed evidence",
        apply=True,
        store=adoption_store,
    )
    authorized = LiveAdoptionGate(adoption_store).authorize(
        adoptable_signal(signal_id),
        strategy_id="validated-signal-shadow",
        strategy_version="strategy-v1",
        runtime_mode=RuntimeMode.SHADOW,
        authorized_at=NOW,
    )
    return live, adoption_store, approval.adoption_decision_id, authorized


def _candidate(authorized, **changes: object) -> TradeCandidate:  # type: ignore[no-untyped-def]
    values: dict[str, object] = {
        "candidate_id": CandidateId("candidate-authorized-1"),
        "strategy_id": "validated-signal-shadow",
        "strategy_version": "strategy-v1",
        "pair": CurrencyPair.parse("USD_JPY"),
        "side": Side.BUY,
        "score": Probability(0.7),
        "signal_ids": (authorized.signal.signal_id,),
        "created_at": NOW,
    }
    values.update(changes)
    return TradeCandidate(**values)  # type: ignore[arg-type]


def test_candidate_persists_exact_signal_authorization_lineage(tmp_path: Path) -> None:
    live, _, _, authorized = _authorized(tmp_path)
    store = SQLiteLiveDecisionStore(live)
    candidate = _candidate(authorized)

    store.append_authorized_candidate(candidate, (authorized,))

    assert store.candidate_authorization_lineage(candidate.candidate_id) == (
        {
            "candidate_id": candidate.candidate_id.value,
            "signal_id": authorized.signal.signal_id.value,
            "authorization_id": authorized.authorization.authorization_id,
            "adoption_decision_id": authorized.authorization.adoption_decision_id,
        },
    )


def test_unauthorized_signal_cannot_be_persisted_by_strict_candidate_path(
    tmp_path: Path,
) -> None:
    live, _, _, authorized = _authorized(tmp_path)
    store = SQLiteLiveDecisionStore(live)
    candidate = _candidate(authorized)

    with pytest.raises(AdoptionRejected) as rejected:
        store.append_authorized_candidate(candidate, ())

    assert rejected.value.reason is AdoptionFailureReason.SIGNAL_SPECIFICATION_MISMATCH
    with pytest.raises(KeyError):
        store.decision_chain(candidate.candidate_id)


def test_authorization_for_another_signal_is_rejected_without_partial_candidate(
    tmp_path: Path,
) -> None:
    live, adoption_store, _, first = _authorized(tmp_path)
    second = LiveAdoptionGate(adoption_store).authorize(
        adoptable_signal("signal-adopted-2"),
        strategy_id="validated-signal-shadow",
        strategy_version="strategy-v1",
        runtime_mode=RuntimeMode.SHADOW,
        authorized_at=NOW,
    )
    store = SQLiteLiveDecisionStore(live)
    candidate = _candidate(first)

    with pytest.raises(AdoptionRejected):
        store.append_authorized_candidate(candidate, (second,))

    with pytest.raises(KeyError):
        store.decision_chain(candidate.candidate_id)


def test_authorization_for_another_strategy_version_is_rejected(tmp_path: Path) -> None:
    live, _, _, authorized = _authorized(tmp_path)
    store = SQLiteLiveDecisionStore(live)
    candidate = _candidate(authorized, strategy_version="strategy-v2")

    with pytest.raises(AdoptionRejected):
        store.append_authorized_candidate(candidate, (authorized,))


def test_revoked_approval_invalidates_stale_authorization_for_new_candidate(
    tmp_path: Path,
) -> None:
    live, adoption_store, approval_id, authorized = _authorized(tmp_path)
    adoption_store.append_revocation(
        revocation_decision(
            adoption_store.get_decision(approval_id),
            decided_at=NOW + timedelta(seconds=1),
            actor="reviewer@example.com",
            reason="superseded",
        )
    )
    candidate = _candidate(
        authorized,
        created_at=NOW + timedelta(seconds=2),
    )

    with pytest.raises(AdoptionRejected) as rejected:
        SQLiteLiveDecisionStore(live).append_authorized_candidate(
            candidate, (authorized,)
        )

    assert rejected.value.reason is AdoptionFailureReason.ADOPTION_REVOKED


def test_authorized_candidate_keeps_portfolio_and_risk_chain_consistent(
    tmp_path: Path,
) -> None:
    live, _, _, authorized = _authorized(tmp_path)
    store = SQLiteLiveDecisionStore(live)
    candidate = _candidate(authorized)
    portfolio = PortfolioDecision(
        decision_id=PortfolioDecisionId("portfolio-authorized-1"),
        candidate_id=candidate.candidate_id,
        disposition=PortfolioDisposition.ACCEPT,
        proposed_quantity=Decimal("1000"),
        reason_code="WITHIN_EXPOSURE_LIMITS",
        exposure_snapshot=CurrencyExposureSnapshot((), NOW),
        created_at=NOW,
    )
    risk = RiskDecision(
        decision_id=RiskDecisionId("risk-authorized-1"),
        portfolio_decision_id=portfolio.decision_id,
        disposition=RiskDisposition.APPROVE,
        reason_code="RISK_APPROVED",
        risk_policy_version="risk-v1",
        created_at=NOW,
    )

    store.append_authorized_candidate(candidate, (authorized,))
    store.append_portfolio_decision(portfolio)
    store.append_risk_decision(risk)

    chain = store.decision_chain(candidate.candidate_id)
    assert chain["candidate_id"] == candidate.candidate_id.value
    assert chain["portfolio"]["candidate_id"] == candidate.candidate_id.value  # type: ignore[index]
    assert chain["risk"]["portfolio_decision_id"] == portfolio.decision_id.value  # type: ignore[index]
