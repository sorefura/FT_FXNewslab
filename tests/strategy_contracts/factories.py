from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fx_core import (
    Currency,
    CurrencyPair,
    FeatureId,
    Horizon,
    PairScore,
    PairTarget,
    Probability,
    Signal,
    SignalId,
    VersionMetadata,
)
from swap_bot.adoption import (
    AdoptionMode,
    AuthorizedSignal,
    RuntimeMode,
    SignalAuthorization,
)
from swap_bot.models import PositionId, Side
from swap_bot.strategy import (
    NEWS_FILTERED_CARRY_CONFIG_VERSION,
    OPERATIONAL_SWAP_EVIDENCE_VERSION,
    PRODUCTION_CANDIDATE_CONTRACT_VERSION,
    NewsFilteredCarryStrategyConfig,
    OperationalSwapEvidence,
    PositionExitEvidenceContext,
    ProductionEntryEvaluationInput,
    ProductionPositionExitEvaluationInput,
)
from swap_bot.swap import SwapAvailability

NOW = datetime(2026, 7, 17, 3, 0, tzinfo=UTC)
PAIR = CurrencyPair.parse("USD_JPY")


def strategy_config(**changes: object) -> NewsFilteredCarryStrategyConfig:
    values: dict[str, object] = {
        "config_contract_version": NEWS_FILTERED_CARRY_CONFIG_VERSION,
        "strategy_id": "news-filtered-carry",
        "strategy_version": "strategy-v1",
        "eligible_pairs": (
            CurrencyPair.parse("USD_JPY"),
            CurrencyPair.parse("MXN_JPY"),
        ),
        "pair_transformation_version": "currency-pair-v1",
        "expected_pair_signal_type": "pair_fundamental",
        "positive_entry_threshold": PairScore(0.5),
        "negative_entry_threshold": PairScore(-0.5),
        "signal_max_age": timedelta(hours=4, microseconds=3),
        "swap_max_age": timedelta(hours=12, microseconds=7),
        "entry_policy_version": "entry-policy-v1",
        "exit_policy_version": "exit-policy-v1",
        "candidate_contract_version": PRODUCTION_CANDIDATE_CONTRACT_VERSION,
        "close_on_signal_reversal": True,
        "close_on_non_positive_carry": True,
        "close_on_missing_or_stale_signal": True,
        "close_on_missing_or_stale_swap": True,
        "maximum_holding_age": timedelta(days=30, microseconds=11),
    }
    values.update(changes)
    return NewsFilteredCarryStrategyConfig(**values)  # type: ignore[arg-type]


def authorized_pair_signal(
    *,
    signal_id: str = "signal-pair-1",
    authorization_id: str = "signal-authorization-1",
    adoption_decision_id: str = "adoption-approval-1",
    evidence_snapshot_id: str = "research-evidence-1",
    pair: CurrencyPair = PAIR,
    strategy_id: str = "news-filtered-carry",
    strategy_version: str = "strategy-v1",
    signal_created_at: datetime = NOW - timedelta(minutes=1),
    authorized_at: datetime = NOW,
) -> AuthorizedSignal:
    signal = Signal(
        signal_id=SignalId(signal_id),
        target=PairTarget(pair),
        signal_type="pair_fundamental",
        direction=PairScore(1.75),
        strength=Probability(0.9),
        confidence=Probability(0.8),
        horizon=Horizon.DAYS_3,
        observed_at=NOW - timedelta(minutes=2),
        created_at=signal_created_at,
        source_feature_ids=(FeatureId("feature-1"), FeatureId("feature-2")),
        versions=VersionMetadata(
            producer_version="producer-v1",
            model_version="model-v1",
            prompt_version="prompt-v1",
            scorer_version="fundamental-scorer-v1",
            transformation_version="currency-pair-v1",
        ),
    )
    authorization = SignalAuthorization(
        authorization_id=authorization_id,
        signal_id=signal.signal_id.value,
        adoption_decision_id=adoption_decision_id,
        evidence_snapshot_id=evidence_snapshot_id,
        adoption_policy_version="adoption-policy-v1",
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        adoption_mode=AdoptionMode.SHADOW_ONLY,
        runtime_mode=RuntimeMode.SHADOW,
        authorized_at=authorized_at,
    )
    return AuthorizedSignal(signal, authorization)


def swap_evidence(**changes: object) -> OperationalSwapEvidence:
    values: dict[str, object] = {
        "evidence_contract_version": OPERATIONAL_SWAP_EVIDENCE_VERSION,
        "pair": PAIR,
        "availability": SwapAvailability.AVAILABLE,
        "long_received_amount": Decimal("12.50"),
        "short_received_amount": Decimal("-15.25"),
        "unit_basis": "JPY_PER_10K_CURRENCY_PER_DAY",
        "settlement_currency": Currency("JPY"),
        "source": "recorded-swap-source",
        "source_version": "recorded-swap-v1",
        "provider_observed_at": NOW - timedelta(seconds=2),
        "received_at": NOW - timedelta(seconds=1),
        "effective_from": NOW - timedelta(days=1),
        "effective_until": NOW + timedelta(days=1),
    }
    values.update(changes)
    return OperationalSwapEvidence.create(**values)  # type: ignore[arg-type]


def entry_input(**changes: object) -> ProductionEntryEvaluationInput:
    values: dict[str, object] = {
        "authorized_pair_signal": authorized_pair_signal(),
        "approved_strategy_config_identity": strategy_config().strategy_config_identity,
        "swap_evidence": swap_evidence(),
        "evaluated_at": NOW + timedelta(seconds=1),
    }
    values.update(changes)
    return ProductionEntryEvaluationInput(**values)  # type: ignore[arg-type]


def position_exit_context(**changes: object) -> PositionExitEvidenceContext:
    values: dict[str, object] = {
        "position_evidence_id": "position-evidence-1",
        "position_opened_at": NOW - timedelta(days=30),
        "position_observed_at": NOW + timedelta(seconds=1),
        "signal_selection_checkpoint_id": "signal-selection-checkpoint-1",
        "swap_selection_checkpoint_id": "swap-selection-checkpoint-1",
        "expected_signal_specification_identity": "signal-specification-1",
        "prior_adoption_decision_id": "adoption-approval-previous",
        "adoption_state_evidence_id": "adoption-state-evidence-1",
        "exit_input_policy_version": "exit-input-policy-v1",
    }
    values.update(changes)
    return PositionExitEvidenceContext(**values)  # type: ignore[arg-type]


def position_exit_input(
    *,
    context_changes: dict[str, object] | None = None,
    **changes: object,
) -> ProductionPositionExitEvaluationInput:
    values: dict[str, object] = {
        "strategy_id": "news-filtered-carry",
        "strategy_version": "strategy-v1",
        "approved_strategy_config_identity": strategy_config().strategy_config_identity,
        "position_id": PositionId("position-1"),
        "pair": PAIR,
        "existing_position_side": Side.BUY,
        "evidence_context": position_exit_context(**(context_changes or {})),
        "authorized_pair_signal": authorized_pair_signal(),
        "swap_evidence": swap_evidence(),
        "evaluated_at": NOW + timedelta(seconds=2),
    }
    values.update(changes)
    return ProductionPositionExitEvaluationInput(**values)  # type: ignore[arg-type]
