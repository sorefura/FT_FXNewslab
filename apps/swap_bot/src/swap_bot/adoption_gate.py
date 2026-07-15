from datetime import datetime

from fx_core import Signal
from fx_core.time import require_utc

from .adoption import (
    AdoptionFailureReason,
    AdoptionMode,
    AdoptionRejected,
    AuthorizedSignal,
    RuntimeMode,
    SignalAuthorization,
    StrategyAdoptionDecision,
    adoption_authority_start,
    digest,
)
from .adoption_store import SQLiteAdoptionStore


class LiveAdoptionGate:
    def __init__(self, store: SQLiteAdoptionStore) -> None:
        self._store = store

    def authorize(
        self,
        signal: Signal,
        *,
        strategy_id: str,
        strategy_version: str,
        runtime_mode: RuntimeMode,
        authorized_at: datetime,
        strategy_config_identity: str | None = None,
    ) -> AuthorizedSignal:
        require_utc(authorized_at, "authorization time")
        try:
            approvals = self._store.list_approvals(
                strategy_id=strategy_id, strategy_version=strategy_version
            )
        except (KeyError, TypeError, ValueError) as error:
            raise AdoptionRejected(
                AdoptionFailureReason.NO_ACTIVE_ADOPTION,
                "persisted adoption state is malformed",
            ) from error
        if not approvals:
            self._reject(
                AdoptionFailureReason.NO_ACTIVE_ADOPTION,
                "no adoption approval exists for the Strategy",
            )
        approvals = tuple(
            approval
            for approval in approvals
            if approval.strategy_config_identity == strategy_config_identity
        )
        if not approvals:
            self._reject(
                AdoptionFailureReason.NO_ACTIVE_ADOPTION,
                "Strategy configuration identity is not approved",
            )
        exact = tuple(
            approval
            for approval in approvals
            if approval.approved_signal_specification.matches_signal(signal)
        )
        if not exact:
            self._reject(
                AdoptionFailureReason.SIGNAL_SPECIFICATION_MISMATCH,
                "Signal semantics do not match an approved specification",
            )
        active: list[StrategyAdoptionDecision] = []
        rejected_reasons: list[AdoptionFailureReason] = []
        for approval in exact:
            reason = self._ineligible_reason(
                approval, signal, runtime_mode=runtime_mode, at=authorized_at
            )
            if reason is None:
                active.append(approval)
            else:
                rejected_reasons.append(reason)
        if len(active) > 1:
            self._reject(
                AdoptionFailureReason.AMBIGUOUS_ADOPTION,
                "multiple active exact approvals match the Signal",
            )
        if not active:
            reason = _dominant_reason(rejected_reasons)
            self._reject(reason, "no exact approval is currently eligible")
        approval = active[0]
        authorization = SignalAuthorization(
            authorization_id="signal-authorization-"
            + digest(
                {
                    "signal_id": signal.signal_id.value,
                    "adoption_decision_id": approval.adoption_decision_id,
                    "strategy_id": strategy_id,
                    "strategy_version": strategy_version,
                    "runtime_mode": runtime_mode.value,
                }
            ),
            signal_id=signal.signal_id.value,
            adoption_decision_id=approval.adoption_decision_id,
            evidence_snapshot_id=approval.evidence_snapshot_id,
            adoption_policy_version=approval.adoption_policy_version,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            adoption_mode=approval.adoption_mode,
            runtime_mode=runtime_mode,
            authorized_at=authorized_at,
        )
        if not self._store.append_authorization(authorization):
            authorization = self._store.get_authorization(
                authorization.authorization_id
            )
        return AuthorizedSignal(signal=signal, authorization=authorization)

    def _ineligible_reason(
        self,
        approval: StrategyAdoptionDecision,
        signal: Signal,
        *,
        runtime_mode: RuntimeMode,
        at: datetime,
    ) -> AdoptionFailureReason | None:
        if self._store.is_revoked_at(approval.adoption_decision_id, at):
            return AdoptionFailureReason.ADOPTION_REVOKED
        authority_start = adoption_authority_start(
            approval.effective_from, approval.decided_at
        )
        if at < authority_start or signal.created_at < authority_start:
            return AdoptionFailureReason.ADOPTION_NOT_YET_EFFECTIVE
        if at >= approval.expires_at:
            return AdoptionFailureReason.ADOPTION_EXPIRED
        if (
            runtime_mode is RuntimeMode.LIVE
            and approval.adoption_mode is AdoptionMode.SHADOW_ONLY
        ):
            return AdoptionFailureReason.ADOPTION_MODE_NOT_ALLOWED
        return None

    @staticmethod
    def _reject(reason: AdoptionFailureReason, detail: str) -> None:
        raise AdoptionRejected(reason, detail)


def _dominant_reason(
    reasons: list[AdoptionFailureReason],
) -> AdoptionFailureReason:
    priority = (
        AdoptionFailureReason.ADOPTION_REVOKED,
        AdoptionFailureReason.ADOPTION_EXPIRED,
        AdoptionFailureReason.ADOPTION_NOT_YET_EFFECTIVE,
        AdoptionFailureReason.ADOPTION_MODE_NOT_ALLOWED,
    )
    return next(
        (reason for reason in priority if reason in reasons),
        AdoptionFailureReason.NO_ACTIVE_ADOPTION,
    )
