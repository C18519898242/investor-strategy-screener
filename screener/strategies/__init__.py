from screener.strategies.base import Strategy, StrategySpec
from screener.strategies.episode_01_graham import GrahamStrategy
from screener.strategies.episode_04_buffett import BuffettStrategy

__all__ = ["Strategy", "StrategySpec", "load_builtin_strategies"]


def load_builtin_strategies() -> tuple[Strategy, ...]:
    return (GrahamStrategy(), BuffettStrategy())
