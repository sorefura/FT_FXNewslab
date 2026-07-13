from unittest.mock import Mock, patch

import pytest
from swap_bot.execution import GmoPrivatePostTransport, LiveArmPolicy


def test_live_submission_requires_configuration_and_environment_arming() -> None:
    client = Mock()
    with patch.dict("os.environ", {}, clear=True):
        transport = GmoPrivatePostTransport(client, LiveArmPolicy(config_enabled=True))
        with pytest.raises(PermissionError):
            transport.post_once("https://example.invalid", data="{}", headers={})
    client.post.assert_not_called()


def test_private_post_timeout_is_not_retried() -> None:
    client = Mock()
    client.post.side_effect = TimeoutError("ambiguous submission")
    with patch.dict("os.environ", {"LIVE_TRADING_ARMED": "YES"}, clear=True):
        transport = GmoPrivatePostTransport(client, LiveArmPolicy(config_enabled=True))
        with pytest.raises(TimeoutError):
            transport.post_once("https://example.invalid", data="{}", headers={})
    assert client.post.call_count == 1

