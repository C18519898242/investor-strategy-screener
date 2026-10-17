from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pandas as pd

from screener.config import Settings
from screener.report import render_html, write_result
from screener.store import last_successful_sync_at, published_datasets
from screener.strategies import Strategy, load_builtin_strategies
from screener.sync import fetch_not_implemented, sync_all


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
    fetch: Callable = fetch_not_implemented,
    clock: Callable[[], str] = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    strategies: tuple[Strategy, ...] | None = None,
    output_root: Path = Path("."),
) -> int:
    strategies = load_builtin_strategies() if strategies is None else strategies
    while True:
        updated = last_successful_sync_at(connection) or "无"
        output_fn("投资人选股策略")
        output_fn(f"本地数据上次更新：{updated}")
        output_fn("1. 更新本地数据")
        output_fn("2. 运行选股")
        output_fn("0. 退出")
        choice = input_fn("请选择：")
        if choice == "1":
            code = _sync_menu(connection, strategies, settings, fetch, clock, input_fn, output_fn)
        elif choice == "2":
            code = _screen_menu(connection, strategies, output_root, input_fn, output_fn)
        elif choice == "0":
            return 0
        else:
            continue
        if code != 0:
            return 1
    return 1


def _sync_menu(connection, strategies, settings, fetch, clock, input_fn, output_fn) -> int:
    updated = last_successful_sync_at(connection) or "无"
    output_fn("更新本地数据")
    output_fn(f"1. 开始更新 ({updated})")
    output_fn("0. 返回主菜单")
    choice = input_fn("请选择：")
    if choice != "1":
        return 0
    datasets = tuple(dict.fromkeys(
        dataset for strategy in strategies for dataset in strategy.spec.required_datasets
    ))
    output_fn("更新范围：所有已实现策略所需的数据")
    output_fn("正在更新共用数据，并补充策略专用数据（如有）……")
    try:
        finished_at = sync_all(
            connection,
            datasets=datasets,
            settings=settings,
            fetch=fetch,
            finished_at=clock(),
        )
    except Exception as error:
        output_fn(f"更新失败：{error}")
        return 1
    output_fn(f"更新完成：{finished_at}")
    input_fn("按回车返回主菜单")
    return 0


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
    output_fn("正在读取本地数据并筛选……")
    try:
        frame = strategy.screen(pd.DataFrame())
    except NotImplementedError as error:
        output_fn(str(error))
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
    output_fn(f"入选股票：{len(rows)} 只")
    output_fn(f"结果文件：{path.as_posix()}")
    input_fn("按回车返回主菜单")
    return 0
