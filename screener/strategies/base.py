import sqlite3
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

    def screen(self, connection: sqlite3.Connection, trade_date: str) -> pd.DataFrame:
        """返回展示用的行；`trade_date` 是 `YYYYMMDD` 的交易日。"""
        raise NotImplementedError
