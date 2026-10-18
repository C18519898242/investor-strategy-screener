import pandas as pd

from screener.strategies.base import Strategy, StrategySpec


class MockAllDatasetsStrategy(Strategy):
    """联调用策略：登记全部数据集，本身不做任何筛选。

    用途：验证数据集能否端到端跑通。菜单「更新本地数据」拉的是策略的
    required_datasets，所以选中这个策略更新，就会把所有已登记的数据集
    一次全拉下来。

    数据名写死、不 import `screener.sync.registry`：从 strategies 导入
    sync 子模块会连带把 tushare 拉进进程（`screener/sync/__init__.py` 会
    再导出客户端），破坏「strategies 不碰网络层」的分层。新增数据集时把
    数据名加到这里即可，`tests/test_strategies.py` 会检查它和 registry 的
    登记表一致，漏改会直接测试失败。
    """

    spec = StrategySpec(
        strategy_id="mock-all-datasets",
        menu_name="【联调】全部数据集",
        output_dir="mock-all-datasets",
        required_datasets=("股票列表", "交易日历", "停复牌"),
        display_fields=(),
    )

    def screen(self, frame: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError("联调用策略不做筛选")
