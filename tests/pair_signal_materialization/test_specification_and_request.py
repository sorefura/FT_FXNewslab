from dataclasses import fields, replace
from datetime import timedelta, timezone
from inspect import Parameter, signature

import pytest
from fx_core import CurrencyPair, Horizon
from fx_signal_store import (
    PairSignalMaterializationRequest,
    PairSignalMaterializationSpecification,
)

from tests.pair_signal_materialization.factories import NOW, request, specification


@pytest.mark.parametrize(
    "change",
    [
        {"pair": CurrencyPair.parse("EUR_JPY")},
        {"horizon": Horizon.DAY_1},
        {"producer_version": "producer-v2"},
        {"model_version": "model-v2"},
        {"prompt_version": "prompt-v2"},
        {"scorer_version": "scorer-v2"},
        {"source_signal_max_age": timedelta(hours=5)},
    ],
)
def test_specification_identity_changes_with_every_semantic_dimension(
    change: dict[str, object],
) -> None:
    assert specification().specification_id != specification(**change).specification_id


def test_same_specification_content_has_same_id_and_integer_microseconds() -> None:
    first = specification(source_signal_max_age=timedelta(seconds=1, microseconds=3))
    second = specification(source_signal_max_age=timedelta(seconds=1, microseconds=3))

    assert first.specification_id == second.specification_id
    assert first.identity_payload["source_signal_max_age_microseconds"] == 1_000_003


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"source_signal_type": "other"}, "source Signal type"),
        ({"output_signal_type": "other"}, "output Signal type"),
        ({"expected_source_transformation_version": "unexpected"}, "must not"),
        ({"output_transformation_version": "pair-v2"}, "transformation"),
        ({"observation_group_policy_version": "partial-v1"}, "group policy"),
        ({"selection_policy_version": "latest-v1"}, "selection policy"),
        ({"source_signal_max_age": timedelta(0)}, "positive"),
        ({"source_signal_max_age": -timedelta(seconds=1)}, "positive"),
        ({"producer_version": " "}, "producer_version"),
    ],
)
def test_specification_rejects_unsupported_or_hidden_semantics(
    change: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        specification(**change)


def test_specification_factory_has_no_semantic_defaults() -> None:
    create_parameters = signature(PairSignalMaterializationSpecification.create).parameters
    assert all(
        parameter.default is Parameter.empty
        for parameter in create_parameters.values()
    )
    assert {item.name for item in fields(specification())} >= {
        "pair",
        "horizon",
        "producer_version",
        "source_signal_max_age",
    }


def test_forged_specification_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="specification_id"):
        replace(specification(), specification_id="pair-signal-spec-forged")


def test_request_identity_is_stable_for_same_pair_as_of_and_specification() -> None:
    assert request().request_id == request().request_id


@pytest.mark.parametrize(
    "change",
    [
        {"pair": CurrencyPair.parse("EUR_JPY")},
        {"as_of": NOW + timedelta(seconds=1)},
        {"specification": specification(model_version="model-v2")},
    ],
)
def test_request_identity_changes_only_with_semantic_request_inputs(
    change: dict[str, object],
) -> None:
    if "pair" in change:
        change["specification"] = specification(pair=change["pair"])
    assert request().request_id != request(**change).request_id


def test_request_rejects_pair_mismatch_non_utc_and_forged_id() -> None:
    with pytest.raises(ValueError, match="does not match specification"):
        request(pair=CurrencyPair.parse("EUR_JPY"))
    with pytest.raises(ValueError, match="UTC"):
        request(as_of=NOW.astimezone(timezone(timedelta(hours=9))))
    with pytest.raises(ValueError, match="request_id"):
        replace(request(), request_id="pair-signal-request-forged")


def test_request_contract_excludes_discovered_and_attempt_inputs() -> None:
    request_fields = {item.name for item in fields(PairSignalMaterializationRequest)}
    assert request_fields == {
        "request_id",
        "contract_version",
        "pair",
        "as_of",
        "specification",
    }
    assert {
        "selected_base_signal_id",
        "selected_quote_signal_id",
        "checkpoint_sequence",
        "captured_at",
        "materialized_at",
        "worker_id",
        "attempt_id",
    }.isdisjoint(request_fields)
