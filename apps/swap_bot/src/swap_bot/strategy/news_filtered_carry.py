from fx_core import PairScore, PairTarget

from ..models import Side
from ..swap import SwapAvailability
from .config import NewsFilteredCarryStrategyConfig
from .contracts import (
    EntrySkipReason,
    ProductionEntryEvaluation,
    ProductionEntryEvaluationInput,
)
from .swap_evidence import OperationalSwapEvidence


class NewsFilteredCarryStrategy:
    def __init__(self, config: NewsFilteredCarryStrategyConfig) -> None:
        if type(config) is not NewsFilteredCarryStrategyConfig:
            raise TypeError("config must be exact NewsFilteredCarryStrategyConfig")
        NewsFilteredCarryStrategyConfig.__post_init__(config)
        self._config = config

    def evaluate_entry(
        self, evaluation_input: ProductionEntryEvaluationInput
    ) -> ProductionEntryEvaluation:
        if type(evaluation_input) is not ProductionEntryEvaluationInput:
            raise TypeError("evaluation_input must be exact ProductionEntryEvaluationInput")
        ProductionEntryEvaluationInput.__post_init__(evaluation_input)

        authorized = evaluation_input.authorized_pair_signal
        signal = authorized.signal
        swap = evaluation_input.swap_evidence

        if type(signal.target) is not PairTarget:
            return self._skip(evaluation_input, EntrySkipReason.SIGNAL_NOT_PAIR_TARGET)
        if evaluation_input.evaluated_pair not in self._config.eligible_pairs:
            return self._skip(evaluation_input, EntrySkipReason.PAIR_NOT_CONFIGURED)
        if signal.target.pair != evaluation_input.evaluated_pair:
            return self._skip(evaluation_input, EntrySkipReason.SIGNAL_NOT_PAIR_TARGET)
        if signal.signal_type != self._config.expected_pair_signal_type:
            return self._skip(evaluation_input, EntrySkipReason.SIGNAL_TYPE_MISMATCH)
        if signal.versions.transformation_version != self._config.pair_transformation_version:
            return self._skip(evaluation_input, EntrySkipReason.TRANSFORMATION_VERSION_MISMATCH)
        if (
            authorized.authorization.strategy_id != self._config.strategy_id
            or authorized.authorization.strategy_version != self._config.strategy_version
        ):
            return self._skip(evaluation_input, EntrySkipReason.SIGNAL_STRATEGY_IDENTITY_MISMATCH)
        if (
            evaluation_input.approved_strategy_config_identity
            != self._config.strategy_config_identity
        ):
            return self._skip(evaluation_input, EntrySkipReason.SIGNAL_CONFIG_IDENTITY_MISMATCH)
        if signal.created_at > evaluation_input.evaluated_at:
            return self._skip(evaluation_input, EntrySkipReason.SIGNAL_IN_FUTURE)
        if evaluation_input.evaluated_at - signal.observed_at > self._config.signal_max_age:
            return self._skip(evaluation_input, EntrySkipReason.SIGNAL_STALE)

        side = self._entry_side(signal.direction)
        if side is None:
            return self._skip(evaluation_input, EntrySkipReason.DIRECTION_NEUTRAL)
        if swap.pair != evaluation_input.evaluated_pair:
            return self._skip(evaluation_input, EntrySkipReason.SWAP_WRONG_PAIR)

        availability_reason = _availability_skip_reason(swap)
        if availability_reason is not None:
            return self._skip(evaluation_input, availability_reason)
        if swap.received_at > evaluation_input.evaluated_at:
            return self._skip(evaluation_input, EntrySkipReason.SWAP_MALFORMED)
        if evaluation_input.evaluated_at < swap.effective_from:
            return self._skip(evaluation_input, EntrySkipReason.SWAP_NOT_APPLICABLE)
        if (
            swap.effective_until is not None
            and evaluation_input.evaluated_at > swap.effective_until
        ) or evaluation_input.evaluated_at - swap.received_at > self._config.swap_max_age:
            return self._skip(evaluation_input, EntrySkipReason.SWAP_STALE)

        required, opposite = (
            (swap.long_received_amount, swap.short_received_amount)
            if side is Side.BUY
            else (swap.short_received_amount, swap.long_received_amount)
        )
        if required is not None and required > 0:
            return ProductionEntryEvaluation.create_candidate(
                evaluation_input,
                candidate_contract_version=self._config.candidate_contract_version,
                side=side,
            )
        if opposite is not None and opposite > 0:
            return self._skip(evaluation_input, EntrySkipReason.DIRECTION_CARRY_MISMATCH)
        return self._skip(evaluation_input, EntrySkipReason.CARRY_NOT_POSITIVE)

    def _entry_side(self, score: object) -> Side | None:
        if type(score) is not PairScore:
            return None
        if score.value > self._config.positive_entry_threshold.value:
            return Side.BUY
        if score.value < self._config.negative_entry_threshold.value:
            return Side.SELL
        return None

    @staticmethod
    def _skip(
        evaluation_input: ProductionEntryEvaluationInput, reason: EntrySkipReason
    ) -> ProductionEntryEvaluation:
        return ProductionEntryEvaluation.create_skip(evaluation_input, reason=reason)


def _availability_skip_reason(
    swap: OperationalSwapEvidence,
) -> EntrySkipReason | None:
    if swap.availability is SwapAvailability.AVAILABLE:
        return None
    if swap.availability is SwapAvailability.UNKNOWN:
        return EntrySkipReason.SWAP_UNKNOWN
    if swap.availability is SwapAvailability.UNAVAILABLE:
        return EntrySkipReason.SWAP_UNAVAILABLE
    if swap.availability is SwapAvailability.NOT_APPLICABLE:
        return EntrySkipReason.SWAP_NOT_APPLICABLE
    if swap.availability is SwapAvailability.STALE:
        return EntrySkipReason.SWAP_STALE
    return EntrySkipReason.SWAP_MALFORMED
