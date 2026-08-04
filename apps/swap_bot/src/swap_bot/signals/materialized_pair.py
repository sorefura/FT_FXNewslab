from datetime import datetime

from fx_core import Currency, CurrencyPair
from fx_core.time import require_utc
from fx_signal_store import (
    OperationalPairSignalMaterializer,
    PairSignalMaterializationRequest,
    PairSignalMaterializerOutcome,
    PairSignalMaterializerResult,
    reconstruct_materialized_pair_signal,
)

from ..adoption import AuthorizedSignal
from ..adoption_gate import LiveAdoptionGate
from ..execution_authority import (
    ExecutionAuthorityMode,
    adoption_runtime_mode_for,
    require_execplan_0006_authority,
)
from ..strategy.config import NewsFilteredCarryStrategyConfig


def authorize_materialized_pair_signal(
    result: object,
    *,
    config: NewsFilteredCarryStrategyConfig,
    pair: CurrencyPair,
    authority: ExecutionAuthorityMode,
    authorized_at: datetime,
    adoption_gate: LiveAdoptionGate,
) -> AuthorizedSignal | None:
    """Authorize one exact M2-B5 Pair Signal after authenticating its full lineage."""
    require_execplan_0006_authority(authority)
    if type(authorized_at) is not datetime:
        raise TypeError("authorized_at must be the exact datetime contract type")
    require_utc(authorized_at, "authorization time")
    if type(config) is not NewsFilteredCarryStrategyConfig:
        raise TypeError("config must be the exact NewsFilteredCarryStrategyConfig")
    NewsFilteredCarryStrategyConfig.__post_init__(config)
    _validate_exact_pair(pair)
    if type(result) is not PairSignalMaterializerResult:
        raise TypeError("materializer result must use the exact supported contract type")
    PairSignalMaterializerResult.validate_intrinsic_integrity(result)
    request = result.request
    specification = request.specification
    if pair not in config.eligible_pairs:
        raise ValueError("configured Pair is not eligible for the Strategy")
    if pair != request.pair or pair != specification.pair:
        raise ValueError("configured Pair does not match materialization evidence")
    if specification.output_signal_type != config.expected_pair_signal_type:
        raise ValueError("materialized Signal type does not match Strategy config")
    if specification.output_transformation_version != config.pair_transformation_version:
        raise ValueError("materialized transformation does not match Strategy config")
    if result.outcome in (
        PairSignalMaterializerOutcome.NO_SELECTION,
        PairSignalMaterializerOutcome.AMBIGUOUS,
    ):
        return None
    signal = reconstruct_materialized_pair_signal(result)
    if signal.created_at > authorized_at:
        raise ValueError("authorization cannot predate reconstructed Signal creation")
    return adoption_gate.authorize(
        signal,
        strategy_id=config.strategy_id,
        strategy_version=config.strategy_version,
        strategy_config_identity=config.strategy_config_identity,
        runtime_mode=adoption_runtime_mode_for(authority),
        authorized_at=authorized_at,
    )


def _validate_exact_pair(pair: object) -> CurrencyPair:
    if type(pair) is not CurrencyPair:
        raise TypeError("pair must use the exact CurrencyPair contract type")
    if type(pair.base) is not Currency or type(pair.quote) is not Currency:
        raise TypeError("Pair currencies must use the exact Currency contract type")
    Currency.__post_init__(pair.base)
    Currency.__post_init__(pair.quote)
    CurrencyPair.__post_init__(pair)
    return pair


class MaterializedPairSignalAuthorizationService:
    def __init__(
        self,
        *,
        materializer: OperationalPairSignalMaterializer,
        adoption_gate: LiveAdoptionGate,
    ) -> None:
        self._materializer = materializer
        self._adoption_gate = adoption_gate

    def run(
        self,
        request: PairSignalMaterializationRequest,
        *,
        config: NewsFilteredCarryStrategyConfig,
        pair: CurrencyPair,
        authority: ExecutionAuthorityMode,
        authorized_at: datetime,
        claim_captured_at: datetime,
        materialized_at_if_selected: datetime | None = None,
    ) -> AuthorizedSignal | None:
        require_execplan_0006_authority(authority)
        result = self._materializer.materialize(
            request,
            claim_captured_at=claim_captured_at,
            materialized_at_if_selected=materialized_at_if_selected,
        )
        return authorize_materialized_pair_signal(
            result,
            config=config,
            pair=pair,
            authority=authority,
            authorized_at=authorized_at,
            adoption_gate=self._adoption_gate,
        )
