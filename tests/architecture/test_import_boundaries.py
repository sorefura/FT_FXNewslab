import ast
from pathlib import Path

import pytest

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


def test_fx_core_does_not_import_infrastructure_or_applications() -> None:
    forbidden = {"sqlite3", "requests", "openai", "swap_bot", "fx_signal_store", "fx_research"}
    for path in (ROOT / "packages/fx_core/src/fx_core").rglob("*.py"):
        imported_roots = {name.split(".")[0] for name in _imports(path)}
        assert forbidden.isdisjoint(imported_roots), f"forbidden import in {path}"


def test_research_and_live_do_not_import_each_other() -> None:
    live_root = ROOT / "apps/swap_bot/src"
    for path in live_root.rglob("*.py"):
        assert "fx_research" not in {name.split(".")[0] for name in _imports(path)}
    research_root = ROOT / "apps/fx_research/src"
    if research_root.exists():
        for path in research_root.rglob("*.py"):
            assert "swap_bot" not in {name.split(".")[0] for name in _imports(path)}


def test_research_consumer_contract_does_not_import_live_application() -> None:
    contract = ROOT / "tests/research_consumer_contract/test_signal_reader_contract.py"
    imported_roots = {name.split(".")[0] for name in _imports(contract)}
    assert "swap_bot" not in imported_roots


def test_signal_store_depends_on_shared_domain_not_applications() -> None:
    forbidden = {"swap_bot", "fx_research"}
    for path in (ROOT / "packages/fx_signal_store/src/fx_signal_store").rglob("*.py"):
        imported_roots = {name.split(".")[0] for name in _imports(path)}
        assert forbidden.isdisjoint(imported_roots), f"forbidden import in {path}"


def test_forward_evaluation_contracts_do_not_leak_into_shared_or_live_packages() -> None:
    for root in (
        ROOT / "packages/fx_core/src/fx_core",
        ROOT / "packages/fx_signal_store/src/fx_signal_store",
        ROOT / "apps/swap_bot/src/swap_bot",
    ):
        for path in root.rglob("*.py"):
            assert "fx_research" not in {
                name.split(".")[0] for name in _imports(path)
            }, f"Research contract import in {path}"


def test_signal_evaluation_does_not_import_live_strategy_or_broker_modules() -> None:
    evaluation_modules = tuple(
        (ROOT / "apps/fx_research/src/fx_research").glob("evaluation*.py")
    )
    forbidden = {"swap_bot", "strategy", "portfolio", "risk", "execution", "ports"}
    for path in evaluation_modules:
        imported = {name.split(".")[-1] for name in _imports(path)}
        assert forbidden.isdisjoint(imported), f"Live dependency in {path}"


def test_portfolio_and_risk_do_not_import_broker_or_execution() -> None:
    for module in ("portfolio.py", "risk.py"):
        imports = _imports(ROOT / "apps/swap_bot/src/swap_bot" / module)
        roots = {name.split(".")[-1] for name in imports}
        assert "execution" not in roots
        assert "ports" not in roots


def test_live_adoption_gate_does_not_import_research_execution_or_broker_ports() -> None:
    imports = _imports(ROOT / "apps/swap_bot/src/swap_bot/adoption_gate.py")
    imported_modules = {name.split(".")[-1] for name in imports}
    assert {"fx_research", "execution", "ports"}.isdisjoint(imported_modules)


def test_production_strategy_contracts_do_not_import_forbidden_layers() -> None:
    strategy_root = ROOT / "apps/swap_bot/src/swap_bot/strategy"
    forbidden_roots = {"fx_research", "openai"}
    forbidden_live_modules = {
        "execution",
        "llm_feature",
        "portfolio",
        "risk",
        "shadow",
    }
    for path in strategy_root.rglob("*.py"):
        imports = _imports(path)
        assert forbidden_roots.isdisjoint(
            {name.split(".")[0] for name in imports}
        ), f"forbidden Strategy import in {path}"
        assert forbidden_live_modules.isdisjoint(
            {name.split(".")[-1] for name in imports}
        ), f"forbidden Live-layer import in {path}"


@pytest.mark.parametrize(
    "relative_path",
    [
        "apps/swap_bot/src/swap_bot/strategy/ordinary_close.py",
        "apps/swap_bot/src/swap_bot/ordinary_close_store.py",
        "apps/swap_bot/src/swap_bot/ordinary_close_application.py",
    ],
)
def test_ordinary_close_module_does_not_import_forbidden_layers_or_transports(
    relative_path: str,
) -> None:
    path = ROOT / relative_path
    imports = _imports(path)
    forbidden_roots = {"fx_research", "openai"}
    forbidden_modules = {
        "portfolio",
        "risk",
        "execution",
        "paper",
        "broker",
        "shadow",
        "llm_feature",
    }
    assert forbidden_roots.isdisjoint({name.split(".")[0] for name in imports})
    assert forbidden_modules.isdisjoint({name.split(".")[-1] for name in imports})


def test_milestone_2c_and_2d_use_only_their_five_additive_live_migrations() -> None:
    strategy_root = ROOT / "apps/swap_bot/src/swap_bot/strategy"
    assert (strategy_root / "news_filtered_carry.py").exists()
    migrations = {
        path.name
        for path in (ROOT / "apps/swap_bot/src/swap_bot/migrations").glob("*.sql")
    }
    assert migrations == {
        "0001_validated_signal_live_adoption.sql",
        "0002_candidate_authorization_integrity.sql",
        "0003_operational_swap_evidence.sql",
        "0004_production_entry_strategy.sql",
        "0005_ordinary_close_path.sql",
    }


def test_milestone_2c_entry_root_does_not_import_downstream_trading_layers() -> None:
    imports = _imports(ROOT / "apps/swap_bot/src/swap_bot/production_entry.py")
    imported_modules = {name.split(".")[-1] for name in imports}
    assert {"portfolio", "risk", "execution", "paper", "broker", "ports"}.isdisjoint(
        imported_modules
    )
