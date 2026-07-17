from enum import StrEnum

from .adoption import RuntimeMode


class ExecutionAuthorityMode(StrEnum):
    SHADOW_NOT_SUBMITTED = "SHADOW_NOT_SUBMITTED"
    PAPER = "PAPER"
    LIVE = "LIVE"


def adoption_runtime_mode_for(authority: ExecutionAuthorityMode) -> RuntimeMode:
    if not isinstance(authority, ExecutionAuthorityMode):
        raise TypeError("authority must be ExecutionAuthorityMode")
    if authority is ExecutionAuthorityMode.LIVE:
        return RuntimeMode.LIVE
    return RuntimeMode.SHADOW


def require_execplan_0006_authority(authority: ExecutionAuthorityMode) -> None:
    if not isinstance(authority, ExecutionAuthorityMode):
        raise TypeError("authority must be ExecutionAuthorityMode")
    if authority is ExecutionAuthorityMode.LIVE:
        raise ValueError("ExecPlan 0006 does not grant LIVE execution authority")
