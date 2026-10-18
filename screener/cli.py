import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pandas as pd

from screener.config import Settings
from screener.logging import setup_logging
from screener.report import render_html, write_result
from screener.store import last_successful_sync_at, published_datasets
from screener.strategies import Strategy, load_builtin_strategies
from screener.sync import dataset_water_levels, fetch_datasets, registered_datasets, sync_all

logger = logging.getLogger(__name__)


class DataNotReadyError(Exception):
    def __init__(self, reason: str):
        if reason not in {"missing", "partial"}:
            raise ValueError(reason)
        self.reason = reason
        super().__init__(reason)


def assert_ready(connection, strategy: Strategy) -> None:
    published = published_datasets(connection)
    if not published:
        raise DataNotReadyError("missing")
    if not set(strategy.spec.required_datasets).issubset(published):
        raise DataNotReadyError("partial")


def main(
    argv=None,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    connection,
    settings: Settings,
    fetch: Callable = fetch_datasets,
    clock: Callable[[], str] = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    strategies: tuple[Strategy, ...] | None = None,
    output_root: Path = Path("."),
    log_file: Path = Path("logs") / "screener.log",
) -> int:
    strategies = load_builtin_strategies() if strategies is None else strategies
    setup_logging(log_file=log_file, console=output_fn)
    logger.info("启动：投资人选股策略")
    while True:
        updated = last_successful_sync_at(connection) or "无"
        output_fn("投资人选股策略")
        output_fn(f"本地数据上次更新：{updated}")
        output_fn("1. 更新本地数据")
        output_fn("2. 运行选股")
        output_fn("0. 退出")
        choice = input_fn("请选择：")
        if choice == "1":
            code = _sync_menu(connection, settings, fetch, clock, input_fn, output_fn)
        elif choice == "2":
            code = _screen_menu(connection, strategies, output_root, input_fn, output_fn)
        elif choice == "0":
            return 0
        else:
            continue
        if code != 0:
            return 1
    return 1


def _sync_menu(connection, settings, fetch, clock, input_fn, output_fn) -> int:
    """列出登记的数据集和各自水位，选一个就只拉那一个；拉完回到这张菜单，0 回主菜单。"""
    datasets = registered_datasets()
    while True:
        levels = dataset_water_levels(connection, datasets)
        output_fn("更新本地数据")
        for index, dataset in enumerate(datasets, start=1):
            output_fn(f"{index}. {dataset}（{levels[dataset]}）")
        output_fn("0. 返回主菜单")
        choice = input_fn("请选择：")
        if choice == "0":
            return 0
        if not choice.isdigit() or not 1 <= int(choice) <= len(datasets):
            output_fn(f"编号应为 0~{len(datasets)} 之间的数字")
            continue
        dataset = datasets[int(choice) - 1]
        try:
            sync_all(
                connection,
                datasets=(dataset,),
                settings=settings,
                fetch=fetch,
                clock=clock,
            )
        except Exception as error:
            logger.error("更新失败：%s", error, exc_info=True)
            return 1
        input_fn("按回车继续")


def _screen_menu(connection, strategies, output_root, input_fn, output_fn) -> int:
    output_fn("选择策略")
    for index, strategy in enumerate(strategies, start=1):
        output_fn(f"{index}. {strategy.spec.menu_name}")
    output_fn("0. 返回主菜单")
    choice = input_fn("请选择：")
    if choice == "0":
        return 0
    strategy = strategies[int(choice) - 1]
    trade_date = input_fn("筛选日期（留空＝上一交易日；格式 YYYY-MM-DD）：")
    if trade_date == "":
        output_fn("本地交易日历尚未就绪")
        return 0
    try:
        datetime.strptime(trade_date, "%Y-%m-%d")
    except ValueError:
        output_fn("日期格式应为 YYYY-MM-DD")
        return 0
    try:
        assert_ready(connection, strategy)
    except DataNotReadyError as error:
        if error.reason == "missing":
            output_fn("请先更新本地数据")
        else:
            output_fn("本地数据不完整，无法筛选")
        return 0
    output_fn(f"使用行情交易日：{trade_date}")
    logger.info("开始筛选：%s（%s）", strategy.spec.menu_name, trade_date)
    output_fn("正在读取本地数据并筛选……")
    try:
        frame = strategy.screen(pd.DataFrame())
    except NotImplementedError as error:
        logger.error("%s", error)
        return 0
    rows = frame.to_dict("records")
    html = render_html(
        strategy_name=strategy.spec.menu_name,
        trade_date=trade_date,
        synced_at=last_successful_sync_at(connection),
        rows=rows,
        display_fields=strategy.spec.display_fields,
    )
    path = write_result(
        output_root,
        output_dir=strategy.spec.output_dir,
        generated_at=datetime.now(),
        html=html,
    )
    logger.info("筛选完成：入选 %d 只，结果 %s", len(rows), path.as_posix())
    input_fn("按回车返回主菜单")
    return 0
