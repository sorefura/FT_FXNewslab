import ast
from pathlib import Path

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
