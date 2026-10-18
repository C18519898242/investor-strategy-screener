from screener.strategies.base import Strategy, StrategySpec
from screener.strategies.episode_01_graham import GrahamStrategy

__all__ = ["Strategy", "StrategySpec", "load_builtin_strategies"]


def load_builtin_strategies() -> tuple[Strategy, ...]:
    return (GrahamStrategy(),)
