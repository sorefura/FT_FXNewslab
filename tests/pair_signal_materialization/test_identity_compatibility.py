from types import MappingProxyType

from fx_core.identity import canonical_json, digest
from swap_bot.adoption import canonical_json as adoption_canonical_json
from swap_bot.adoption import digest as adoption_digest

from tests.strategy_contracts.factories import strategy_config


def test_canonical_digest_preserves_pre_extraction_characterization() -> None:
    payloads = (
        (
            {"b": {"z": 1, "a": [True, None, "x"]}, "a": 2},
            '{"a":2,"b":{"a":[true,null,"x"],"z":1}}',
            "dbbfb193740627429b2473d55032ac8bbb4cfb3b8eb9fbaeaff46592431d3e63",
        ),
        (
            MappingProxyType(
                {"outer": MappingProxyType({"value": 3}), "items": ("a", False)}
            ),
            '{"items":["a",false],"outer":{"value":3}}',
            "c7a2fa5601393552dd83f107fad5e7ab8366a3b61a70bff279914805fff4e390",
        ),
        (
            {"none": None, "bool": True, "int": 7, "float": 1.25, "string": "yen"},
            '{"bool":true,"float":1.25,"int":7,"none":null,"string":"yen"}',
            "1125639abee85ef70f140762b52b323b07bff74643d0dfe7923b7cae4bcd28aa",
        ),
    )
    for payload, expected_json, expected_digest in payloads:
        assert canonical_json(payload) == expected_json
        assert digest(payload) == expected_digest


def test_key_order_tuple_and_list_keep_existing_semantics() -> None:
    first = {"b": 2, "a": {"items": ("USD", 1, None)}}
    second = {"a": {"items": ["USD", 1, None]}, "b": 2}

    assert canonical_json(first) == canonical_json(second)
    assert digest(first) == digest(second)
    assert digest({"items": ("USD", 1, None)}) == (
        "95170e1489588a728d88a4084bb9ede2f7f12a7f758be7bf1be570ac5ae63acf"
    )
    assert canonical_json({"string": "円"}) == '{"string":"\\u5186"}'


def test_swap_bot_digest_api_remains_byte_compatible_with_shared_helper() -> None:
    payload = {
        "decision_type": "APPROVED_FOR_STRATEGY",
        "evidence_snapshot_id": "research-evidence-1",
        "policy_version": "adoption-policy-v1",
        "policy_hash": "abc123",
    }

    assert adoption_canonical_json(payload) == canonical_json(payload)
    assert adoption_digest(payload) == digest(payload)
    assert "adoption-approval-" + adoption_digest(payload) == (
        "adoption-approval-bd7ad398a5f9a5916d3c2eda698267ddd1f85e276f529a402e58121bc1a102de"
    )


def test_existing_milestone_2a_config_identity_is_unchanged() -> None:
    assert strategy_config().strategy_config_identity == (
        "strategy-config-c930f8385b8ae0a6173382144299e94c00ae30d8db0cfecc5a8ef75beb0b879b"
    )
