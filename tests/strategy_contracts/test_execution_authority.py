import pytest
from swap_bot.adoption import RuntimeMode
from swap_bot.execution_authority import (
    ExecutionAuthorityMode,
    adoption_runtime_mode_for,
    require_execplan_0006_authority,
)


@pytest.mark.parametrize(
    ("authority", "expected"),
    [
        (ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED, RuntimeMode.SHADOW),
        (ExecutionAuthorityMode.PAPER, RuntimeMode.SHADOW),
        (ExecutionAuthorityMode.LIVE, RuntimeMode.LIVE),
    ],
)
def test_execution_authority_maps_to_distinct_adoption_runtime(
    authority: ExecutionAuthorityMode, expected: RuntimeMode
) -> None:
    assert adoption_runtime_mode_for(authority) is expected


def test_execplan_0006_rejects_live_before_composition() -> None:
    with pytest.raises(ValueError, match="does not grant LIVE"):
        require_execplan_0006_authority(ExecutionAuthorityMode.LIVE)


def test_execplan_0006_accepts_non_live_authorities() -> None:
    require_execplan_0006_authority(ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED)
    require_execplan_0006_authority(ExecutionAuthorityMode.PAPER)


def test_adoption_runtime_has_no_paper_mode() -> None:
    assert "PAPER" not in RuntimeMode.__members__


def test_unknown_execution_authority_fails_closed() -> None:
    with pytest.raises(TypeError, match="ExecutionAuthorityMode"):
        adoption_runtime_mode_for("PAPER")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ExecutionAuthorityMode"):
        require_execplan_0006_authority("SHADOW_NOT_SUBMITTED")  # type: ignore[arg-type]
