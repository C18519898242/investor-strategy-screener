from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    menu_name: str
    output_dir: str
    required_datasets: tuple[str, ...]
    display_fields: tuple[str, ...]


class Strategy:
    spec: StrategySpec

    def screen(self, frame: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError
