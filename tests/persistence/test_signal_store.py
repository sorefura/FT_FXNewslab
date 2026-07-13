import sqlite3
from pathlib import Path

import pytest
from fx_core import Horizon, SignalId
from fx_signal_store import SQLiteSignalStore

from tests.factories import feature, observation, signal


def _populated_store(tmp_path: Path) -> SQLiteSignalStore:
    path = tmp_path / "signals.sqlite3"
    store = SQLiteSignalStore(path)
    store.append_observation(observation())
    store.append_feature(feature())
    store.append_signal(signal())
    return store


def test_signal_store_traces_signal_to_feature_and_observation(tmp_path: Path) -> None:
    store = _populated_store(tmp_path)
    lineage = store.get_lineage(SignalId("signal-1"))
    assert [item.value for item in lineage.feature_ids] == ["feature-1"]
    assert [item.value for item in lineage.observation_ids] == ["obs-1"]


def test_signal_store_filters_by_target_horizon_and_scorer_version(tmp_path: Path) -> None:
    store = _populated_store(tmp_path)
    results = store.list_signals(
        target="USD", horizon=Horizon.DAYS_3, scorer_version="scorer-v1"
    )
    assert [item.signal_id.value for item in results] == ["signal-1"]


def test_signal_store_rejects_signal_update_and_delete(tmp_path: Path) -> None:
    store = _populated_store(tmp_path)
    with sqlite3.connect(store.path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE signals SET direction = 0 WHERE id = 'signal-1'")
    with sqlite3.connect(store.path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM signals WHERE id = 'signal-1'")
