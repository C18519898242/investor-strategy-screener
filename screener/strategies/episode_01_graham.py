"""第 1 集：本杰明·格雷厄姆。计算口径见 strategies.md 的「我们的实现口径」。

八项按 `E01-1`～`E01-8` 判定，任一必要数据缺失或任一数值不满足就排除，所以
「用布尔掩码取交集」与「逐项短路」结果相同，这里用前者——一次算完便于核对。

几处口径上的关键约定（都在 strategies.md 里写过，改代码前先看）：

- **单季利润表**：`income.report_type='2'`，滚动 12 个月净利润是最近 4 个连续单季之和；
  年度净利润是同一年 4 个单季之和，缺季度就不算数。
- **近 3 年平均市盈率**：12 个季度样本，每个样本用「该季度内最后一个未停牌交易日的
  总市值 ÷ 含该季度的最近 4 个单季之和」，所以需要连续 15 个季度；不能用
  `daily_basic.pe_ttm` 代替，它不含后来才公布的财报。
- **市净率**：分母用最近一期资产负债表的 `total_hldr_eqy_exc_min_int`，不用
  `daily_basic.pb`（官方定义是「净资产－其他权益工具」，与本集口径不同）。
- **长期负债**：本集取「非流动负债合计」，本地表没有这一列，用
  `total_liab - total_cur_liab` 得到（2026-10-22 拿 9 个报告期与接口值核对，差 0.00）。
- **股息率**：同一会计年度（`end_date` 的年份）全部现金分红方案合计 ÷ 该年度最后一个
  未停牌交易日的总市值。上游只存了已实施方案，未实施的预案不计。
- **单位**：`daily_basic.total_mv` 与 `dividend.base_share` 都是万元，
  `cash_div_tax × base_share` 直接得万元分红总额，两者相除不用换算；
  总市值阈值 50 亿元要换成 5,000,000,000 元比较。
"""

import sqlite3

import pandas as pd

from screener.strategies.base import Strategy, StrategySpec

GRAHAM_MAX = 22.5
AVERAGE_PE_MAX = 15.0
DIVIDEND_YIELD_MIN = 0.01
CURRENT_RATIO_MIN = 2.0
WORKING_CAPITAL_RATIO_MIN = 1.0
MARKET_CAP_MIN_YUAN = 5_000_000_000.0
MARKET_CAP_MIN_WAN = MARKET_CAP_MIN_YUAN / 10_000.0

AVERAGE_SAMPLES = 12
QUARTERS_NEEDED = AVERAGE_SAMPLES + 3
ANNUAL_YEARS = 3

DISPLAY_FIELDS: tuple[str, ...] = (
    "格雷厄姆数",
    "当前市盈率（TTM）",
    "市净率",
    "近 3 年平均市盈率",
    "近 3 年 ROE",
    "近 3 年归母净利润",
    "近 3 年股息率",
    "流动比率",
    "营运资本/长期负债",
    "总市值",
)

_EMPTY = pd.DataFrame(columns=["code", "name", "graham"])


def _query(connection: sqlite3.Connection, sql: str, params=()) -> pd.DataFrame:
    return pd.read_sql_query(sql, connection, params=params)


def _year_of(period: str) -> str:
    return period[:4]


def _quarter_start(period: str) -> str:
    """报告期所在季度的第一天，用来限定取价窗口。"""
    return f"{_year_of(period)}{period[4:6]}01"


def _universe(connection: sqlite3.Connection, day: str) -> pd.DataFrame:
    """股票池：主板/创业板/科创板 A 股，排除银行保险和当日停牌。

    ST 在「股票列表」更新时已经按 `stock_st` 从 `stock_basic` 删掉了，这里不再判名称。
    """
    frame = _query(
        connection,
        """
        SELECT ts_code AS code, name FROM stock_basic
        WHERE market IN ('主板', '创业板', '科创板')
          AND ts_code NOT LIKE '900%' AND ts_code NOT LIKE '200%'
          AND (industry IS NULL OR industry NOT IN ('银行', '保险'))
        """,
    )
    suspended = _query(
        connection, "SELECT ts_code AS code FROM suspend_d WHERE trade_date = ?", (day,)
    )
    return frame[~frame["code"].isin(set(suspended["code"]))].reset_index(drop=True)


def _quarterly_periods(connection: sqlite3.Connection, day: str) -> list[str]:
    """本地已有、报告期末不晚于筛选日的单季报告期，从新到旧，只到参考报告期为止。"""
    reference = _reference_period(connection, day)
    rows = connection.execute(
        """
        SELECT DISTINCT end_date FROM income
        WHERE report_type = '2' AND end_date <= ?
        ORDER BY end_date DESC
        """,
        (reference,),
    ).fetchall()
    return [row[0] for row in rows]


def _reference_period(connection: sqlite3.Connection, day: str) -> str:
    """筛选日能看到的最新「披露完一轮」的报告期。

    直接取 `MAX(end_date)` 会踩坑：报告季刚开头的几天里，`income` 已经有几百家先披露
    的当期数据，拿它当基准会把 97% 的股票判成「数据缺失」（2026-10-22 实测：10/16 时
    20260930 只有 111 家）。所以从新到旧找第一个覆盖率不低于上一期 90% 的报告期——
    报告季没结束时覆盖率会明显低于上一期，一轮披露完成后两期基本持平。

    用相对覆盖率而不是「占股票总数之比」，是因为历史日期的股票池本来就更小。
    """
    rows = connection.execute(
        """
        SELECT end_date, COUNT(DISTINCT ts_code) FROM income
        WHERE report_type = '2' AND end_date <= ?
        GROUP BY end_date ORDER BY end_date DESC LIMIT 6
        """,
        (day,),
    ).fetchall()
    if not rows:
        return day
    for index in range(len(rows) - 1):
        period, covered = rows[index]
        if covered >= 0.9 * rows[index + 1][1]:
            return period
    return rows[-1][0]


def _quarterly_income(connection: sqlite3.Connection, periods: list[str]) -> pd.DataFrame:
    """单季归母净利润，行是股票、列是报告期（从新到旧）。"""
    placeholders = ", ".join("?" * len(periods))
    frame = _query(
        connection,
        "SELECT ts_code AS code, end_date, n_income_attr_p AS profit FROM income"
        f" WHERE report_type = '2' AND end_date IN ({placeholders})",
        tuple(periods),
    )
    return frame.pivot_table(
        index="code", columns="end_date", values="profit", aggfunc="last"
    ).reindex(columns=periods)


def _rolling_profit(matrix: pd.DataFrame, samples: int) -> pd.DataFrame:
    """每个样本的滚动 12 个月净利润（万元）；缺任何一个季度就是 NaN。"""
    return pd.DataFrame(
        {
            f"ttm{index}": matrix.iloc[:, index : index + 4].sum(axis=1, min_count=4)
            for index in range(samples)
        }
    )


def _annual_profit(matrix: pd.DataFrame) -> pd.DataFrame:
    """按年把单季净利润加总；一年不足 4 个季度记 NaN。"""
    transposed = matrix.transpose()
    transposed.index = pd.Index([_year_of(period) for period in transposed.index])
    return transposed.groupby(level=0).sum(min_count=4).transpose()


def _last_market_cap(
    connection: sqlite3.Connection, codes: set[str], start: str, end: str
) -> dict[str, float]:
    """期间内最后一个该股未停牌且有行情的交易日，返回总市值（万元）。"""
    days = [
        row[0]
        for row in connection.execute(
            """
            SELECT cal_date FROM trade_cal
            WHERE exchange = 'SSE' AND is_open = 1 AND cal_date BETWEEN ? AND ?
            ORDER BY cal_date DESC
            """,
            (start, end),
        )
    ]
    found: dict[str, float] = {}
    for day in days:
        missing = codes - set(found)
        if not missing:
            break
        suspended = {
            row[0]
            for row in connection.execute(
                "SELECT ts_code FROM suspend_d WHERE trade_date = ?", (day,)
            )
        }
        for code, market_cap in connection.execute(
            "SELECT ts_code, total_mv FROM daily_basic WHERE trade_date = ?", (day,)
        ):
            if code in missing and code not in suspended and market_cap is not None:
                found[code] = market_cap
    return found


def _market_caps(
    connection: sqlite3.Connection, codes: set[str], windows: dict[str, tuple[str, str]]
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            label: _last_market_cap(connection, codes, start, end)
            for label, (start, end) in windows.items()
        }
    )


def _dividend_amounts(
    connection: sqlite3.Connection, first_year: str, last_year: str
) -> pd.DataFrame:
    """每年现金分红总额（万元），行是股票、列是年份；没有记录的年份不出现。"""
    frame = _query(
        connection,
        """
        SELECT ts_code AS code, end_date, cash_div_tax, base_share FROM dividend
        WHERE end_date BETWEEN ? AND ?
        """,
        (f"{first_year}0101", f"{last_year}1231"),
    )
    if frame.empty:
        return pd.DataFrame()
    frame["year"] = frame["end_date"].str[:4]
    frame["amount"] = frame["cash_div_tax"].fillna(0.0) * frame["base_share"].fillna(0.0)
    return frame.pivot_table(index="code", columns="year", values="amount", aggfunc="sum")


def _ratio_above(numerator: pd.Series, denominator: pd.Series, limit: float) -> pd.Series:
    """分母为 0 时的口径：分子 > 0 视为满足，分子 ≤ 0 视为不满足；分母 < 0 排除。"""
    return (
        (
            ((denominator > 0) & ((numerator / denominator) > limit))
            | ((denominator == 0) & (numerator > 0))
        )
        & denominator.notna()
        & numerator.notna()
        & (denominator >= 0)
    )


def evaluate(connection: sqlite3.Connection, trade_date: str) -> pd.DataFrame:
    """算出全部通过的股票；任何一项不满足或数据不全的直接不返回。

    `trade_date` 是 `YYYYMMDD`，且必须是一个交易日。
    """
    universe = _universe(connection, trade_date)
    if universe.empty:
        return _EMPTY.copy()

    quarterly = _quarterly_periods(connection, trade_date)
    if len(quarterly) < QUARTERS_NEEDED:
        return _EMPTY.copy()
    window = quarterly[:QUARTERS_NEEDED]
    samples = window[:AVERAGE_SAMPLES]
    annuals = [period for period in quarterly if period.endswith("1231")][:ANNUAL_YEARS]
    if len(annuals) < ANNUAL_YEARS:
        return _EMPTY.copy()

    income = _quarterly_income(connection, window)
    ttm = _rolling_profit(income, AVERAGE_SAMPLES)
    yearly = _annual_profit(income)

    prices = _query(
        connection,
        "SELECT ts_code AS code, total_mv FROM daily_basic WHERE trade_date = ?",
        (trade_date,),
    ).set_index("code")["total_mv"]

    balance_period = connection.execute(
        "SELECT MAX(end_date) FROM balancesheet WHERE end_date <= ?",
        (_reference_period(connection, trade_date),),
    ).fetchone()[0]
    balance = _query(
        connection,
        """
        SELECT ts_code AS code, total_cur_assets, total_cur_liab, total_liab,
               total_hldr_eqy_exc_min_int
        FROM balancesheet WHERE end_date = ?
        """,
        (balance_period,),
    ).set_index("code")

    placeholders = ", ".join("?" * len(annuals))
    roe = _query(
        connection,
        "SELECT ts_code AS code, end_date, roe_waa FROM fina_indicator"
        f" WHERE end_date IN ({placeholders})",
        tuple(annuals),
    ).pivot_table(index="code", columns="end_date", values="roe_waa", aggfunc="last")
    roe = roe.reindex(columns=annuals)

    windows = {period: (_quarter_start(period), period) for period in samples}
    windows.update({f"year{period}": (f"{_year_of(period)}0101", period) for period in annuals})
    caps = _market_caps(connection, set(universe["code"]), windows)
    year_caps = caps[[f"year{period}" for period in annuals]].copy()
    year_caps.columns = annuals

    dividends = _dividend_amounts(connection, _year_of(annuals[-1]), _year_of(annuals[0]))

    table = universe.set_index("code")
    table["market_cap"] = prices
    # income 的金额是元、daily_basic 的总市值是万元，先换算到同一单位再相除。
    table["pe_ttm"] = prices / (ttm["ttm0"] / 10_000.0)
    table["pb"] = prices / (balance["total_hldr_eqy_exc_min_int"] / 10_000.0)
    table["graham"] = table["pe_ttm"] * table["pb"]

    sample_pe = pd.DataFrame(
        {
            period: caps[period] / (ttm[f"ttm{index}"] / 10_000.0)
            for index, period in enumerate(samples)
        }
    ).reindex(table.index)
    table["average_pe"] = sample_pe.mean(axis=1)
    samples_ok = (sample_pe > 0).all(axis=1) & sample_pe.notna().all(axis=1)

    roe_ok = ((roe > 0).all(axis=1) & roe.notna().all(axis=1)).reindex(table.index).fillna(False)
    years = [_year_of(period) for period in annuals]
    profit_ok = (
        ((yearly[years] > 0).all(axis=1) & yearly[years].notna().all(axis=1))
        .reindex(table.index)
        .fillna(False)
    )

    for period in annuals:
        key = _year_of(period)
        amount = (
            dividends[key].reindex(year_caps.index).fillna(0.0)
            if key in dividends.columns
            else pd.Series(0.0, index=year_caps.index)
        )
        table[f"yield{key}"] = amount / year_caps[period]
        table[f"roe{key}"] = roe[period]
        table[f"profit{key}"] = yearly[key]

    dividend_ok = pd.concat(
        [table[f"yield{_year_of(year)}"] > DIVIDEND_YIELD_MIN for year in annuals], axis=1
    ).all(axis=1)

    current_assets = balance["total_cur_assets"].reindex(table.index)
    current_liabilities = balance["total_cur_liab"].reindex(table.index)
    non_current = (balance["total_liab"] - balance["total_cur_liab"]).reindex(table.index)
    working_capital = current_assets - current_liabilities
    table["current_ratio"] = current_assets / current_liabilities
    table["working_capital_ratio"] = working_capital / non_current

    passed = table[
        (table["graham"] < GRAHAM_MAX)
        & (table["pe_ttm"] > 0)
        & samples_ok
        & (table["average_pe"] < AVERAGE_PE_MAX)
        & roe_ok
        & profit_ok
        & dividend_ok
        & _ratio_above(current_assets, current_liabilities, CURRENT_RATIO_MIN)
        & _ratio_above(working_capital, non_current, WORKING_CAPITAL_RATIO_MIN)
        & (balance["total_hldr_eqy_exc_min_int"].reindex(table.index) > 0)
        & (table["market_cap"] > MARKET_CAP_MIN_WAN)
    ].dropna(subset=["graham", "pe_ttm", "pb", "average_pe"])

    return passed.sort_values(["graham"], kind="stable").reset_index()


def _percent(value: float) -> str:
    """`fina_indicator.roe_waa` 和股息率都已经是百分数口径之外的小数，统一乘 100。"""
    return f"{value * 100:.2f}%"


def _roe_percent(value: float) -> str:
    """`roe_waa` 本身就是百分数（19.32 表示 19.32%），不再乘 100。"""
    return f"{value:.2f}%"


def _yuan_to_yi(value: float) -> str:
    return f"{value / 100_000_000:.2f} 亿元"


def _years(annuals: list[str], values: dict[str, float], render) -> str:
    """把三年的数值拼成 `2023 x / 2024 y / 2025 z`。"""
    return " / ".join(f"{_year_of(period)} {render(values[period])}" for period in annuals)


def screen(connection: sqlite3.Connection, trade_date: str) -> pd.DataFrame:
    """筛选并按展示口径格式化，返回给报告用的行。"""
    passed = evaluate(connection, trade_date)
    if passed.empty:
        return passed

    annuals = [
        period for period in _quarterly_periods(connection, trade_date) if period.endswith("1231")
    ][:ANNUAL_YEARS]

    rows = []
    for row in passed.itertuples(index=False):
        roe = {period: getattr(row, f"roe{_year_of(period)}") for period in annuals}
        profit = {period: getattr(row, f"profit{_year_of(period)}") for period in annuals}
        yields = {period: getattr(row, f"yield{_year_of(period)}") for period in annuals}
        rows.append(
            {
                "code": row.code,
                "name": row.name,
                "格雷厄姆数": f"{row.graham:.2f}",
                "当前市盈率（TTM）": f"{row.pe_ttm:.2f}",
                "市净率": f"{row.pb:.2f}",
                "近 3 年平均市盈率": f"{row.average_pe:.2f}",
                "近 3 年 ROE": _years(annuals, roe, _roe_percent),
                "近 3 年归母净利润": _years(annuals, profit, _yuan_to_yi),
                "近 3 年股息率": _years(annuals, yields, _percent),
                "流动比率": f"{row.current_ratio:.2f}",
                "营运资本/长期负债": f"{row.working_capital_ratio:.2f}",
                "总市值": f"{row.market_cap / 10000:.2f} 亿元",
            }
        )
    return pd.DataFrame(rows)


class GrahamStrategy(Strategy):
    spec = StrategySpec(
        strategy_id="episode-01-graham",
        menu_name="格雷厄姆（可用）",
        output_dir="episode-01-graham",
        required_datasets=(
            "股票列表",
            "交易日历",
            "停复牌",
            "每日指标",
            "利润表",
            "资产负债表",
            "财务指标",
            "分红送配",
        ),
        display_fields=DISPLAY_FIELDS,
    )

    def screen(self, connection: sqlite3.Connection, trade_date: str) -> pd.DataFrame:
        return screen(connection, trade_date)
