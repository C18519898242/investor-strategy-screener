from screener.strategies.base import Strategy, StrategySpec
from screener.strategies.episode_01_graham import GrahamStrategy
from screener.strategies.mock_all_datasets import MockAllDatasetsStrategy

__all__ = ["Strategy", "StrategySpec", "load_builtin_strategies"]


def load_builtin_strategies() -> tuple[Strategy, ...]:
    return (GrahamStrategy(), MockAllDatasetsStrategy())
