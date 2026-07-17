import ast
from dataclasses import fields
from pathlib import Path

from fx_core import Signal

ROOT = Path(__file__).parents[2]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_pair_materialization_contracts_import_no_application_package() -> None:
    module = ROOT / "packages/fx_signal_store/src/fx_signal_store/pair_materialization.py"
    imported_roots = {name.split(".")[0] for name in _imports(module)}
    assert {"swap_bot", "fx_research"}.isdisjoint(imported_roots)


def test_shared_identity_imports_no_application_or_infrastructure_package() -> None:
    module = ROOT / "packages/fx_core/src/fx_core/identity.py"
    imported_roots = {name.split(".")[0] for name in _imports(module)}
    assert {"swap_bot", "fx_research", "fx_signal_store", "sqlite3"}.isdisjoint(
        imported_roots
    )


def test_milestone_2b2a_store_imports_no_application_or_materializer_logic() -> None:
    module = ROOT / "packages/fx_signal_store/src/fx_signal_store/store.py"
    imported_roots = {name.split(".")[0] for name in _imports(module)}
    source = module.read_text(encoding="utf-8")

    assert {"swap_bot", "fx_research"}.isdisjoint(imported_roots)
    assert "CurrencyPairSignalTransformer" not in source
    assert "resolve_pair_signal_selection" not in source


def test_milestone_2b2a_adds_only_claim_migration_and_keeps_shared_signal_lineage() -> None:
    signal_fields = {item.name for item in fields(Signal)}
    assert "source_signal_ids" not in signal_fields
    migrations = {
        path.name
        for path in (ROOT / "packages/fx_signal_store/src/fx_signal_store/migrations").glob(
            "*.sql"
        )
    }
    assert migrations == {
        "0001_signal_lineage.sql",
        "0002_pair_materialization_persistence.sql",
    }


def test_milestone_2b1_adds_no_materializer_or_concrete_strategy() -> None:
    assert not (
        ROOT / "packages/fx_signal_store/src/fx_signal_store/materializer.py"
    ).exists()
    assert not (
        ROOT / "apps/swap_bot/src/swap_bot/strategy/news_filtered_carry.py"
    ).exists()
