import pandas as pd

from screener.strategies.base import Strategy, StrategySpec


class GrahamStrategy(Strategy):
    spec = StrategySpec(
        strategy_id="episode-01-graham",
        menu_name="格雷厄姆（可用）",
        output_dir="episode-01-graham",
        required_datasets=(),
        display_fields=(),
    )

    def screen(self, frame: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError("第 1 集筛选尚未实现")
