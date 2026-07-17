from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from fx_core import ObservationId
from fx_signal_store import (
    PAIR_SIGNAL_SELECTION_SNAPSHOT_VERSION,
    PairSignalSelectionOutcome,
    PairSignalSelectionReason,
    PairSignalSelectionSnapshot,
    SourceSignalRole,
    resolve_pair_signal_selection,
)

from tests.pair_signal_materialization.factories import (
    NOW,
    candidate,
    request,
    source_snapshot,
)


def _candidate(
    role: SourceSignalRole,
    *,
    identifier: str,
    group: str,
    sequence: int,
    observed_at: datetime = NOW - timedelta(hours=1),
    created_at: datetime = NOW - timedelta(minutes=30),
    stale: bool = False,
):
    materialization_request = request()
    if stale:
        observed_at = NOW - timedelta(hours=5)
        created_at = NOW - timedelta(hours=4, minutes=59)
    return candidate(
        role,
        materialization_request=materialization_request,
        snapshot=source_snapshot(
            role,
            identifier=identifier,
            observation_ids=(ObservationId(group),),
            observed_at=observed_at,
            created_at=created_at,
        ),
        store_sequence=sequence,
    )


def _resolve(*items):
    materialization_request = request()
    rebound = tuple(
        candidate(
            item.role,
            materialization_request=materialization_request,
            snapshot=item.signal_snapshot,
            store_sequence=item.store_sequence,
        )
        for item in items
    )
    return resolve_pair_signal_selection(
        materialization_request,
        max((item.store_sequence for item in rebound), default=0),
        NOW + timedelta(minutes=1),
        rebound,
    )


@pytest.mark.parametrize(
    ("items", "reason"),
    [
        ((), PairSignalSelectionReason.NO_ELIGIBLE_BASE_SIGNAL),
        (
            (
                _candidate(
                    SourceSignalRole.BASE,
                    identifier="signal-base-stale",
                    group="observation-a",
                    sequence=1,
                    stale=True,
                ),
                _candidate(
                    SourceSignalRole.QUOTE,
                    identifier="signal-quote-stale",
                    group="observation-a",
                    sequence=2,
                    stale=True,
                ),
            ),
            PairSignalSelectionReason.NO_ELIGIBLE_BASE_SIGNAL,
        ),
        (
            (
                _candidate(
                    SourceSignalRole.QUOTE,
                    identifier="signal-quote-only",
                    group="observation-a",
                    sequence=1,
                ),
            ),
            PairSignalSelectionReason.NO_ELIGIBLE_BASE_SIGNAL,
        ),
        (
            (
                _candidate(
                    SourceSignalRole.BASE,
                    identifier="signal-base-only",
                    group="observation-a",
                    sequence=1,
                ),
            ),
            PairSignalSelectionReason.NO_ELIGIBLE_QUOTE_SIGNAL,
        ),
        (
            (
                _candidate(
                    SourceSignalRole.BASE,
                    identifier="signal-base-a",
                    group="observation-a",
                    sequence=1,
                ),
                _candidate(
                    SourceSignalRole.QUOTE,
                    identifier="signal-quote-b",
                    group="observation-b",
                    sequence=2,
                ),
            ),
            PairSignalSelectionReason.NO_COMPLETE_OBSERVATION_GROUP,
        ),
    ],
)
def test_resolver_derives_no_match_from_complete_inventory(items, reason) -> None:
    snapshot = _resolve(*items)

    assert snapshot.outcome is PairSignalSelectionOutcome.NO_MATCH
    assert snapshot.reason is reason
    assert bool(snapshot.candidates) is bool(items)


@pytest.mark.parametrize(
    ("items", "reason"),
    [
        (
            (
                _candidate(
                    SourceSignalRole.BASE,
                    identifier="signal-base-1",
                    group="observation-a",
                    sequence=1,
                ),
                _candidate(
                    SourceSignalRole.BASE,
                    identifier="signal-base-2",
                    group="observation-a",
                    sequence=2,
                ),
                _candidate(
                    SourceSignalRole.QUOTE,
                    identifier="signal-quote-1",
                    group="observation-a",
                    sequence=3,
                ),
            ),
            PairSignalSelectionReason.AMBIGUOUS_BASE_SIGNAL,
        ),
        (
            (
                _candidate(
                    SourceSignalRole.BASE,
                    identifier="signal-base-1",
                    group="observation-a",
                    sequence=1,
                ),
                _candidate(
                    SourceSignalRole.QUOTE,
                    identifier="signal-quote-1",
                    group="observation-a",
                    sequence=2,
                ),
                _candidate(
                    SourceSignalRole.QUOTE,
                    identifier="signal-quote-2",
                    group="observation-a",
                    sequence=3,
                ),
            ),
            PairSignalSelectionReason.AMBIGUOUS_QUOTE_SIGNAL,
        ),
    ],
)
def test_resolver_fails_closed_on_duplicate_role_within_complete_group(
    items, reason
) -> None:
    snapshot = _resolve(*items)

    assert snapshot.outcome is PairSignalSelectionOutcome.AMBIGUOUS
    assert snapshot.reason is reason


def test_base_ambiguity_precedes_quote_ambiguity_across_groups() -> None:
    snapshot = _resolve(
        _candidate(
            SourceSignalRole.BASE,
            identifier="signal-base-a1",
            group="observation-a",
            sequence=1,
        ),
        _candidate(
            SourceSignalRole.BASE,
            identifier="signal-base-a2",
            group="observation-a",
            sequence=2,
        ),
        _candidate(
            SourceSignalRole.QUOTE,
            identifier="signal-quote-a",
            group="observation-a",
            sequence=3,
        ),
        _candidate(
            SourceSignalRole.BASE,
            identifier="signal-base-b",
            group="observation-b",
            sequence=4,
        ),
        _candidate(
            SourceSignalRole.QUOTE,
            identifier="signal-quote-b1",
            group="observation-b",
            sequence=5,
        ),
        _candidate(
            SourceSignalRole.QUOTE,
            identifier="signal-quote-b2",
            group="observation-b",
            sequence=6,
        ),
    )

    assert snapshot.reason is PairSignalSelectionReason.AMBIGUOUS_BASE_SIGNAL


def test_older_ambiguous_group_is_not_hidden_by_newer_unique_group() -> None:
    older = NOW - timedelta(hours=2)
    newer = NOW - timedelta(minutes=20)
    snapshot = _resolve(
        _candidate(
            SourceSignalRole.BASE,
            identifier="signal-base-old-1",
            group="observation-old",
            sequence=1,
            observed_at=older,
            created_at=older + timedelta(minutes=1),
        ),
        _candidate(
            SourceSignalRole.BASE,
            identifier="signal-base-old-2",
            group="observation-old",
            sequence=2,
            observed_at=older,
            created_at=older + timedelta(minutes=1),
        ),
        _candidate(
            SourceSignalRole.QUOTE,
            identifier="signal-quote-old",
            group="observation-old",
            sequence=3,
            observed_at=older,
            created_at=older + timedelta(minutes=1),
        ),
        _candidate(
            SourceSignalRole.BASE,
            identifier="signal-base-new",
            group="observation-new",
            sequence=4,
            observed_at=newer,
            created_at=newer + timedelta(minutes=1),
        ),
        _candidate(
            SourceSignalRole.QUOTE,
            identifier="signal-quote-new",
            group="observation-new",
            sequence=5,
            observed_at=newer,
            created_at=newer + timedelta(minutes=1),
        ),
    )

    assert snapshot.reason is PairSignalSelectionReason.AMBIGUOUS_BASE_SIGNAL


def test_resolver_ranks_complete_groups_by_observed_then_available_time() -> None:
    same_observed = NOW - timedelta(hours=1)
    snapshot = _resolve(
        _candidate(
            SourceSignalRole.BASE,
            identifier="signal-base-a",
            group="observation-a",
            sequence=1,
            observed_at=same_observed,
            created_at=NOW - timedelta(minutes=50),
        ),
        _candidate(
            SourceSignalRole.QUOTE,
            identifier="signal-quote-a",
            group="observation-a",
            sequence=2,
            observed_at=same_observed,
            created_at=NOW - timedelta(minutes=50),
        ),
        _candidate(
            SourceSignalRole.BASE,
            identifier="signal-base-b",
            group="observation-b",
            sequence=3,
            observed_at=same_observed,
            created_at=NOW - timedelta(minutes=20),
        ),
        _candidate(
            SourceSignalRole.QUOTE,
            identifier="signal-quote-b",
            group="observation-b",
            sequence=4,
            observed_at=same_observed,
            created_at=NOW - timedelta(minutes=20),
        ),
    )

    assert snapshot.outcome is PairSignalSelectionOutcome.SELECTED
    assert snapshot.selected_observation_group_identity == next(
        item.observation_group_identity
        for item in snapshot.candidates
        if item.signal_snapshot.signal_id.value == "signal-base-b"
    )


def test_greatest_group_observed_at_precedes_available_time() -> None:
    older = NOW - timedelta(hours=2)
    newer = NOW - timedelta(hours=1)
    snapshot = _resolve(
        _candidate(
            SourceSignalRole.BASE,
            identifier="signal-base-old",
            group="observation-old",
            sequence=1,
            observed_at=older,
            created_at=NOW - timedelta(minutes=5),
        ),
        _candidate(
            SourceSignalRole.QUOTE,
            identifier="signal-quote-old",
            group="observation-old",
            sequence=2,
            observed_at=older,
            created_at=NOW - timedelta(minutes=5),
        ),
        _candidate(
            SourceSignalRole.BASE,
            identifier="signal-base-new",
            group="observation-new",
            sequence=3,
            observed_at=newer,
            created_at=NOW - timedelta(minutes=30),
        ),
        _candidate(
            SourceSignalRole.QUOTE,
            identifier="signal-quote-new",
            group="observation-new",
            sequence=4,
            observed_at=newer,
            created_at=NOW - timedelta(minutes=30),
        ),
    )

    selected = next(
        item
        for item in snapshot.candidates
        if item.candidate_id == snapshot.selected_base_candidate_id
    )
    assert selected.signal_snapshot.signal_id.value == "signal-base-new"


def test_semantic_rank_tie_is_ambiguous_and_ids_do_not_break_it() -> None:
    items = (
        _candidate(
            SourceSignalRole.BASE,
            identifier="signal-z-base",
            group="observation-a",
            sequence=1,
        ),
        _candidate(
            SourceSignalRole.QUOTE,
            identifier="signal-z-quote",
            group="observation-a",
            sequence=2,
        ),
        _candidate(
            SourceSignalRole.BASE,
            identifier="signal-a-base",
            group="observation-b",
            sequence=3,
        ),
        _candidate(
            SourceSignalRole.QUOTE,
            identifier="signal-a-quote",
            group="observation-b",
            sequence=4,
        ),
    )

    first = _resolve(*items)
    second = _resolve(*reversed(items))

    assert first.outcome is PairSignalSelectionOutcome.AMBIGUOUS
    assert first.reason is PairSignalSelectionReason.AMBIGUOUS_SOURCE_GROUP
    assert first.selection_snapshot_id == second.selection_snapshot_id


def test_resolver_retains_ineligible_candidates_in_inventory_identity() -> None:
    base = _candidate(
        SourceSignalRole.BASE,
        identifier="signal-base",
        group="observation-a",
        sequence=1,
    )
    quote = _candidate(
        SourceSignalRole.QUOTE,
        identifier="signal-quote",
        group="observation-a",
        sequence=2,
    )
    stale = _candidate(
        SourceSignalRole.BASE,
        identifier="signal-base-stale",
        group="observation-a",
        sequence=3,
        stale=True,
    )

    without_stale = _resolve(base, quote)
    with_stale = _resolve(base, quote, stale)

    assert with_stale.outcome is PairSignalSelectionOutcome.SELECTED
    assert len(with_stale.candidates) == 3
    assert with_stale.candidate_set_hash != without_stale.candidate_set_hash
    assert with_stale.selection_snapshot_id != without_stale.selection_snapshot_id


def test_manual_terminal_results_and_selected_lineage_cannot_be_forged() -> None:
    selected = _resolve(
        _candidate(
            SourceSignalRole.BASE,
            identifier="signal-base",
            group="observation-a",
            sequence=1,
        ),
        _candidate(
            SourceSignalRole.QUOTE,
            identifier="signal-quote",
            group="observation-a",
            sequence=2,
        ),
    )
    with pytest.raises(ValueError, match="complete candidate inventory"):
        PairSignalSelectionSnapshot.create(
            contract_version=PAIR_SIGNAL_SELECTION_SNAPSHOT_VERSION,
            request=selected.request,
            checkpoint_sequence=selected.checkpoint_sequence,
            captured_at=selected.captured_at,
            candidates=selected.candidates,
            outcome=PairSignalSelectionOutcome.NO_MATCH,
            reason=PairSignalSelectionReason.NO_COMPLETE_OBSERVATION_GROUP,
        )
    with pytest.raises(ValueError, match="complete candidate inventory"):
        replace(selected, reason=PairSignalSelectionReason.NO_COMPLETE_OBSERVATION_GROUP)
    with pytest.raises(ValueError, match="complete candidate inventory"):
        replace(selected, outcome=PairSignalSelectionOutcome.AMBIGUOUS)
    with pytest.raises(ValueError, match="complete candidate inventory"):
        replace(
            selected,
            selected_base_candidate_id=selected.selected_quote_candidate_id,
        )


def test_manual_selected_result_cannot_override_ambiguous_or_incomplete_inventory() -> None:
    ambiguous = _resolve(
        _candidate(
            SourceSignalRole.BASE,
            identifier="signal-base-1",
            group="observation-a",
            sequence=1,
        ),
        _candidate(
            SourceSignalRole.BASE,
            identifier="signal-base-2",
            group="observation-a",
            sequence=2,
        ),
        _candidate(
            SourceSignalRole.QUOTE,
            identifier="signal-quote",
            group="observation-a",
            sequence=3,
        ),
    )
    base = next(
        item for item in ambiguous.candidates if item.role is SourceSignalRole.BASE
    )
    quote = next(
        item for item in ambiguous.candidates if item.role is SourceSignalRole.QUOTE
    )
    with pytest.raises(ValueError, match="complete candidate inventory"):
        PairSignalSelectionSnapshot.create(
            contract_version=PAIR_SIGNAL_SELECTION_SNAPSHOT_VERSION,
            request=ambiguous.request,
            checkpoint_sequence=ambiguous.checkpoint_sequence,
            captured_at=ambiguous.captured_at,
            candidates=ambiguous.candidates,
            outcome=PairSignalSelectionOutcome.SELECTED,
            reason=PairSignalSelectionReason.SELECTED_EXACT_GROUP,
            selected_base_candidate_id=base.candidate_id,
            selected_quote_candidate_id=quote.candidate_id,
            selected_base_signal_id=base.signal_snapshot.signal_id,
            selected_quote_signal_id=quote.signal_snapshot.signal_id,
            selected_observation_group_identity=base.observation_group_identity,
        )

    incomplete = _resolve(
        _candidate(
            SourceSignalRole.BASE,
            identifier="signal-base-a",
            group="observation-a",
            sequence=1,
        ),
        _candidate(
            SourceSignalRole.QUOTE,
            identifier="signal-quote-b",
            group="observation-b",
            sequence=2,
        ),
    )
    base = next(
        item for item in incomplete.candidates if item.role is SourceSignalRole.BASE
    )
    quote = next(
        item for item in incomplete.candidates if item.role is SourceSignalRole.QUOTE
    )
    with pytest.raises(ValueError, match="complete candidate inventory"):
        PairSignalSelectionSnapshot.create(
            contract_version=PAIR_SIGNAL_SELECTION_SNAPSHOT_VERSION,
            request=incomplete.request,
            checkpoint_sequence=incomplete.checkpoint_sequence,
            captured_at=incomplete.captured_at,
            candidates=incomplete.candidates,
            outcome=PairSignalSelectionOutcome.SELECTED,
            reason=PairSignalSelectionReason.SELECTED_EXACT_GROUP,
            selected_base_candidate_id=base.candidate_id,
            selected_quote_candidate_id=quote.candidate_id,
            selected_base_signal_id=base.signal_snapshot.signal_id,
            selected_quote_signal_id=quote.signal_snapshot.signal_id,
            selected_observation_group_identity=base.observation_group_identity,
        )


def test_resolver_rejects_candidate_newer_than_checkpoint() -> None:
    materialization_request = request()
    base = candidate(
        SourceSignalRole.BASE,
        materialization_request=materialization_request,
        store_sequence=2,
    )

    with pytest.raises(ValueError, match="newer than selection checkpoint"):
        resolve_pair_signal_selection(
            materialization_request,
            1,
            NOW + timedelta(minutes=1),
            (base,),
        )
