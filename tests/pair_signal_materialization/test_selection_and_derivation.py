from dataclasses import replace
from datetime import timedelta, timezone

import pytest
from fx_core import (
    FeatureId,
    ObservationId,
    PairScore,
    PairTarget,
    Probability,
    Signal,
    SignalId,
    VersionMetadata,
)
from fx_core.identity import digest
from fx_signal_store import (
    PAIR_SIGNAL_SELECTION_SNAPSHOT_VERSION,
    PairSignalCandidateEligibility,
    PairSignalCandidateRejectionReason,
    PairSignalDerivation,
    PairSignalSelectionOutcome,
    PairSignalSelectionReason,
    PairSignalSelectionSnapshot,
    SignalContentSnapshot,
    SignalLineage,
    SourceSignalRole,
    expected_pair_signal_id,
    inspect_source_candidate,
    observation_group_identity,
    pair_signal_identity_payload,
    resolve_pair_signal_selection,
)

from tests.pair_signal_materialization.factories import (
    NOW,
    candidate,
    pair_signal_snapshot,
    request,
    selected_snapshot,
    source_snapshot,
)


def test_selection_snapshot_canonicalizes_inventory_and_excludes_captured_at_from_id() -> None:
    materialization_request = request()
    base = candidate(SourceSignalRole.BASE, materialization_request=materialization_request)
    quote = candidate(SourceSignalRole.QUOTE, materialization_request=materialization_request)
    first = selected_snapshot(
        materialization_request=materialization_request,
        base=base,
        quote=quote,
        captured_at=NOW + timedelta(minutes=1),
    )
    second = selected_snapshot(
        materialization_request=materialization_request,
        base=base,
        quote=quote,
        captured_at=NOW + timedelta(minutes=5),
    )

    assert first.candidates == (base, quote)
    assert first.candidate_set_hash == second.candidate_set_hash
    assert first.selection_snapshot_id == second.selection_snapshot_id


def test_selection_identity_changes_with_checkpoint_candidate_set_and_outcome() -> None:
    selected = selected_snapshot()
    later_checkpoint = selected_snapshot(checkpoint_sequence=3)
    extra = candidate(
        SourceSignalRole.BASE,
        materialization_request=selected.request,
        snapshot=source_snapshot(SourceSignalRole.BASE, identifier="signal-base-2"),
        store_sequence=3,
    )
    expanded = resolve_pair_signal_selection(
        selected.request,
        3,
        selected.captured_at,
        selected.candidates + (extra,),
    )
    no_match = resolve_pair_signal_selection(
        selected.request,
        2,
        selected.captured_at,
        (),
    )

    assert selected.selection_snapshot_id != later_checkpoint.selection_snapshot_id
    assert expanded.outcome is PairSignalSelectionOutcome.AMBIGUOUS
    assert expanded.reason is PairSignalSelectionReason.AMBIGUOUS_BASE_SIGNAL
    assert selected.selection_snapshot_id != expanded.selection_snapshot_id
    assert selected.selection_snapshot_id != no_match.selection_snapshot_id


def test_selection_rejects_candidate_request_sequence_and_inventory_conflicts() -> None:
    selected = selected_snapshot()
    foreign = candidate(
        SourceSignalRole.BASE,
        materialization_request=request(as_of=NOW - timedelta(seconds=1)),
    )
    with pytest.raises(ValueError, match="another request"):
        PairSignalSelectionSnapshot.create(
            contract_version=PAIR_SIGNAL_SELECTION_SNAPSHOT_VERSION,
            request=selected.request,
            checkpoint_sequence=2,
            captured_at=selected.captured_at,
            candidates=(foreign,),
            outcome=PairSignalSelectionOutcome.NO_MATCH,
            reason=PairSignalSelectionReason.NO_ELIGIBLE_QUOTE_SIGNAL,
        )
    with pytest.raises(ValueError, match="newer than"):
        PairSignalSelectionSnapshot.create(
            contract_version=PAIR_SIGNAL_SELECTION_SNAPSHOT_VERSION,
            request=selected.request,
            checkpoint_sequence=1,
            captured_at=selected.captured_at,
            candidates=selected.candidates,
            outcome=PairSignalSelectionOutcome.NO_MATCH,
            reason=PairSignalSelectionReason.NO_COMPLETE_OBSERVATION_GROUP,
        )
    with pytest.raises(ValueError, match="candidate IDs"):
        PairSignalSelectionSnapshot.create(
            contract_version=PAIR_SIGNAL_SELECTION_SNAPSHOT_VERSION,
            request=selected.request,
            checkpoint_sequence=2,
            captured_at=selected.captured_at,
            candidates=(selected.candidates[0], selected.candidates[0]),
            outcome=PairSignalSelectionOutcome.NO_MATCH,
            reason=PairSignalSelectionReason.NO_ELIGIBLE_QUOTE_SIGNAL,
        )


def test_selected_requires_exact_eligible_base_quote_and_observation_group() -> None:
    selected = selected_snapshot()
    base = selected.candidates[0]
    quote_other_group = candidate(
        SourceSignalRole.QUOTE,
        materialization_request=selected.request,
        snapshot=source_snapshot(
            SourceSignalRole.QUOTE,
            observation_ids=(ObservationId("observation-other"),),
        ),
        store_sequence=2,
    )
    no_complete_group = selected_snapshot(
        materialization_request=selected.request,
        base=base,
        quote=quote_other_group,
    )
    assert no_complete_group.outcome is PairSignalSelectionOutcome.NO_MATCH
    assert no_complete_group.reason is (
        PairSignalSelectionReason.NO_COMPLETE_OBSERVATION_GROUP
    )
    stale = inspect_source_candidate(
        selected.request,
        SourceSignalRole.QUOTE,
        source_snapshot(
            SourceSignalRole.QUOTE,
            observed_at=NOW - timedelta(hours=5),
            created_at=NOW - timedelta(hours=4, minutes=59),
        ),
        2,
    )
    assert stale.eligibility is PairSignalCandidateEligibility.INELIGIBLE
    assert stale.rejection_reason is PairSignalCandidateRejectionReason.STALE_AT_AS_OF
    missing_quote = selected_snapshot(
        materialization_request=selected.request,
        base=base,
        quote=stale,
    )
    assert missing_quote.outcome is PairSignalSelectionOutcome.NO_MATCH
    assert missing_quote.reason is PairSignalSelectionReason.NO_ELIGIBLE_QUOTE_SIGNAL


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (PairSignalSelectionOutcome.NO_MATCH, PairSignalSelectionReason.AMBIGUOUS_SOURCE_GROUP),
        (
            PairSignalSelectionOutcome.AMBIGUOUS,
            PairSignalSelectionReason.NO_COMPLETE_OBSERVATION_GROUP,
        ),
        (
            PairSignalSelectionOutcome.SELECTED,
            PairSignalSelectionReason.NO_COMPLETE_OBSERVATION_GROUP,
        ),
    ],
)
def test_selection_rejects_unsupported_outcome_reason_combinations(
    outcome: PairSignalSelectionOutcome,
    reason: PairSignalSelectionReason,
) -> None:
    selected = selected_snapshot()
    with pytest.raises(ValueError):
        PairSignalSelectionSnapshot.create(
            contract_version=PAIR_SIGNAL_SELECTION_SNAPSHOT_VERSION,
            request=selected.request,
            checkpoint_sequence=2,
            captured_at=selected.captured_at,
            candidates=selected.candidates,
            outcome=outcome,
            reason=reason,
        )


def test_non_selected_outcome_prohibits_selected_lineage() -> None:
    selected = selected_snapshot()
    with pytest.raises(ValueError, match="complete candidate inventory"):
        PairSignalSelectionSnapshot.create(
            contract_version=PAIR_SIGNAL_SELECTION_SNAPSHOT_VERSION,
            request=selected.request,
            checkpoint_sequence=2,
            captured_at=selected.captured_at,
            candidates=selected.candidates,
            outcome=PairSignalSelectionOutcome.NO_MATCH,
            reason=PairSignalSelectionReason.NO_COMPLETE_OBSERVATION_GROUP,
            selected_base_candidate_id=selected.selected_base_candidate_id,
        )


def test_selection_rejects_invalid_time_forged_hash_and_forged_id() -> None:
    with pytest.raises(ValueError, match="before request"):
        selected_snapshot(captured_at=NOW - timedelta(seconds=1))
    with pytest.raises(ValueError, match="UTC"):
        selected_snapshot(captured_at=NOW.astimezone(timezone(timedelta(hours=9))))
    selected = selected_snapshot()
    with pytest.raises(ValueError, match="candidate_set_hash"):
        replace(selected, candidate_set_hash="candidate-set-forged")
    with pytest.raises(ValueError, match="selection_snapshot_id"):
        replace(selected, selection_snapshot_id="pair-signal-selection-forged")


def test_pair_signal_id_is_deterministic_and_commits_to_exact_selected_lineage() -> None:
    selection = selected_snapshot()
    materialized_at = NOW + timedelta(minutes=2)

    first = expected_pair_signal_id(
        selection.request, selection, materialized_at=materialized_at
    )
    second = expected_pair_signal_id(
        selection.request, selection, materialized_at=materialized_at
    )
    later = expected_pair_signal_id(
        selection.request,
        selection,
        materialized_at=materialized_at + timedelta(microseconds=1),
    )

    assert first == second
    assert first != later
    payload = pair_signal_identity_payload(
        selection.request, selection, materialized_at=materialized_at
    )
    assert "direction" not in payload
    assert "pair_score" not in payload
    assert payload["base_signal_content_hash"] != payload["quote_signal_content_hash"]


def test_pair_signal_id_rejects_non_selected_foreign_or_early_inputs() -> None:
    selection = selected_snapshot()
    no_match = resolve_pair_signal_selection(
        selection.request,
        2,
        selection.captured_at,
        (),
    )
    with pytest.raises(ValueError, match="SELECTED"):
        expected_pair_signal_id(selection.request, no_match, materialized_at=NOW)
    with pytest.raises(ValueError, match="another request"):
        expected_pair_signal_id(
            request(as_of=NOW - timedelta(seconds=1)),
            selection,
            materialized_at=NOW + timedelta(minutes=2),
        )
    with pytest.raises(ValueError, match="before request"):
        expected_pair_signal_id(
            selection.request,
            selection,
            materialized_at=NOW - timedelta(seconds=1),
        )


def test_pair_signal_derivation_preserves_exact_ordered_source_lineage() -> None:
    selection = selected_snapshot()
    materialized_at = NOW + timedelta(minutes=2)
    pair_snapshot = pair_signal_snapshot(selection, materialized_at=materialized_at)

    first = PairSignalDerivation.create(
        pair_signal_snapshot=pair_snapshot,
        selection_snapshot=selection,
        materialized_at=materialized_at,
    )
    second = PairSignalDerivation.create(
        pair_signal_snapshot=pair_snapshot,
        selection_snapshot=selection,
        materialized_at=materialized_at,
    )

    assert first == second
    assert first.base_signal_id == selection.selected_base_signal_id
    assert first.quote_signal_id == selection.selected_quote_signal_id
    assert first.base_signal_id != first.quote_signal_id
    assert first.observation_group_identity == observation_group_identity(
        first.observation_ids
    )
    assert first.identity_payload["base_source"]["role"] == "BASE"  # type: ignore[index]
    assert first.identity_payload["quote_source"]["role"] == "QUOTE"  # type: ignore[index]


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("pair_signal_content_hash",), "signal-content-other"),
        (("base_source", "signal_content_hash"), "signal-content-other"),
        (("quote_source", "signal_content_hash"), "signal-content-other"),
        (("selection_snapshot_id",), "selection-other"),
        (("materialized_at",), (NOW + timedelta(minutes=3)).isoformat()),
    ],
)
def test_derivation_identity_changes_with_semantic_lineage(
    path: tuple[str, ...], value: object,
) -> None:
    selection = selected_snapshot()
    materialized_at = NOW + timedelta(minutes=2)
    derivation = PairSignalDerivation.create(
        pair_signal_snapshot=pair_signal_snapshot(selection, materialized_at=materialized_at),
        selection_snapshot=selection,
        materialized_at=materialized_at,
    )
    changed = {**derivation.identity_payload}
    if len(path) == 1:
        changed[path[0]] = value
    else:
        nested = dict(changed[path[0]])  # type: ignore[arg-type]
        nested[path[1]] = value
        changed[path[0]] = nested

    assert digest(changed) != digest(derivation.identity_payload)


def test_derivation_rejects_forged_or_invalid_source_group_and_time() -> None:
    selection = selected_snapshot()
    materialized_at = NOW + timedelta(minutes=2)
    derivation = PairSignalDerivation.create(
        pair_signal_snapshot=pair_signal_snapshot(selection, materialized_at=materialized_at),
        selection_snapshot=selection,
        materialized_at=materialized_at,
    )
    with pytest.raises(ValueError, match="derivation_id"):
        replace(derivation, derivation_id="pair-signal-derivation-forged")
    with pytest.raises(ValueError, match="must differ"):
        replace(
            derivation,
            quote_signal_id=derivation.base_signal_id,
            derivation_id=derivation.expected_derivation_id,
        )
    with pytest.raises(ValueError, match="canonical ordering"):
        observations = (ObservationId("observation-z"), ObservationId("observation-a"))
        replace(
            derivation,
            observation_ids=observations,
            observation_group_identity=observation_group_identity(observations),
            derivation_id="pair-signal-derivation-temporary",
        )
    with pytest.raises(ValueError, match="predates request"):
        replace(
            derivation,
            materialized_at=NOW - timedelta(seconds=1),
            derivation_id="pair-signal-derivation-temporary",
        )


def test_pair_signal_derivation_rejects_wrong_pair_signal_id_or_feature_lineage() -> None:
    selection = selected_snapshot()
    materialized_at = NOW + timedelta(minutes=2)
    pair_snapshot = pair_signal_snapshot(selection, materialized_at=materialized_at)
    with pytest.raises(ValueError, match="signal_id"):
        PairSignalDerivation.create(
            pair_signal_snapshot=_changed_pair_snapshot(
                pair_snapshot,
                selection,
                signal_id=SignalId("pair-signal-other"),
            ),
            selection_snapshot=selection,
            materialized_at=materialized_at,
        )
    with pytest.raises(ValueError, match="source_feature_ids"):
        PairSignalDerivation.create(
            pair_signal_snapshot=_changed_pair_snapshot(
                pair_snapshot,
                selection,
                source_feature_ids=(FeatureId("feature-other"),),
            ),
            selection_snapshot=selection,
            materialized_at=materialized_at,
        )


def _changed_pair_snapshot(
    snapshot: SignalContentSnapshot,
    selection: PairSignalSelectionSnapshot,
    *,
    signal_id: SignalId | None = None,
    source_feature_ids: tuple[FeatureId, ...] | None = None,
) -> SignalContentSnapshot:
    features = source_feature_ids or snapshot.source_feature_ids
    signal = Signal(
        signal_id=signal_id or snapshot.signal_id,
        target=PairTarget(selection.pair),
        signal_type=snapshot.signal_type,
        direction=PairScore(snapshot.direction_value),
        strength=Probability(snapshot.strength),
        confidence=Probability(snapshot.confidence),
        horizon=snapshot.horizon,
        observed_at=snapshot.observed_at,
        created_at=snapshot.created_at,
        source_feature_ids=features,
        versions=VersionMetadata(
            producer_version=snapshot.producer_version,
            model_version=snapshot.model_version,
            prompt_version=snapshot.prompt_version,
            scorer_version=snapshot.scorer_version,
            transformation_version=snapshot.transformation_version,
        ),
    )
    return SignalContentSnapshot.from_signal(
        signal,
        SignalLineage(
            signal_id=signal.signal_id,
            feature_ids=features,
            observation_ids=snapshot.source_observation_ids,
        ),
    )
