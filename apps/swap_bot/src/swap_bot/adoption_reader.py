import sqlite3
from contextlib import closing
from pathlib import Path

from .adoption import StrategyAdoptionDecision
from .adoption_store import SQLiteAdoptionStore


class SQLiteAdoptionDecisionReader:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def get_decision(self, decision_id: str) -> StrategyAdoptionDecision:
        uri = f"file:{self.path.resolve().as_posix()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM live_strategy_adoption_decisions "
                "WHERE adoption_decision_id = ?",
                (decision_id,),
            ).fetchone()
        if row is None:
            raise KeyError(decision_id)
        return SQLiteAdoptionStore._decision_from_row(row)
