"""第 4 集：沃伦·巴菲特。八项规则见 strategies.md 的「视频中的量化筛选条件」。

实现口径（视频没交代的部分按项目通用原则定，缺失一律排除，不补位、不跳过）：

- **年报口径**：`income`、`cashflow` 都只存单季（`report_type='2'`），一年的金额是同年
  4 个单季之和；缺任何一个季度，该年就是缺失，直接排除该股票。`fina_indicator` 的
  `roe_waa`、`roic`、`eps` 按年报期（`1231`）直接取。
- **基准报告期**：不能直接用 `MAX(end_date)`。A 股 2025 年报最晚 2026-04-30 披露完，
  而 2026 三季报在 10 月才陆续出，所以从新到旧找第一个「覆盖率不低于上一期 90%」的
  报告期当基准，年报只取基准期之前（含）的 `1231` 期。这样报告季没走完时不会把大批
  股票误判成缺数据。
- **`E04-3` ROIC**：`roic` 是百分数，接受 `15 < roic <= 100`。上限是异常值挡板——
  投入资本趋零时比率会炸成假高值（2025 年报最高 113.25），这种公司不是优质公司；
  `< 15` 的本来就被阈值淘汰，不用单独设下限。
- **`E04-4` 自由现金流净现比**：`(经营现金流净额 - 资本支出) / 归母净利润 > 0.5`，
  取自同一年报。`free_cashflow` 上游恒为空，不能用；净利润必须为正，否则比值没有意义
  （直接排除，不按负数比较）。
- **`E04-5` 资本支出比率**：`资本支出 / 营业收入 < 20%`。资本支出取
  `c_pay_acq_const_fiolta`（购建固定资产、无形资产和其他长期资产支付的现金）。
- **`E04-6` EPS 复合增速**：窗口是 5 年（视频写 10 年，本项目有意改为 5 年，见
  strategies.md 的视频核对说明）。取最近 5 个年报的 `eps`，首尾两端都必须为正，
  `(末年 / 首年) ** (1/4) - 1 > 5%`；5 个年报不齐（上市不满 5 年）的股票排除。
- **`E04-7` 派息率**：每个年报年度单独算 `现金分红总额 / 归母净利润 > 30%`，5 年都要
  满足。现金分红用 `dividend.cash_div_tax × base_share`（万元）换算成元；某年分红表里
  没有记录就是 0 元（不是数据缺失），该年直接不达标。
- **`E04-8` 总市值**：`daily_basic.total_mv` 是万元，50 亿元 = 500,000 万元。
- **股票池**：与第 1 集一致——主板/创业板/科创板，剔除银行、保险（自由现金流与资本
  支出对它们没有意义）和当日停牌股。ST 在更新股票列表时已剔除。
- **市盈率、市净率只作参考，不参与筛选**：口径与第 1 集相同——`PE_TTM = 总市值 /
  最近 4 个单季归母净利润之和`，`PB = 总市值 / 最近一期归母股东权益`；没有正值时显示
  「—」。加这两列只是让人一眼看到估值，改它们不会影响入选名单。
"""

import sqlite3

import pandas as pd

from screener.strategies.base import Strategy, StrategySpec

ROE_MIN = 15.0
ROE_AVERAGE_MIN = 12.0
ROE_AVERAGE_YEARS = 3
ROIC_MIN = 15.0
ROIC_MAX = 100.0
FREE_CASH_FLOW_RATIO_MIN = 0.5
CAPEX_RATIO_MAX = 0.20
EPS_CAGR_MIN = 0.05
PAYOUT_MIN = 0.30
YEARS_NEEDED = 5
MARKET_CAP_MIN_YUAN = 5_000_000_000.0
MARKET_CAP_MIN_WAN = MARKET_CAP_MIN_YUAN / 10_000.0

_QUARTER_SUFFIXES = ("0331", "0630", "0930", "1231")

DISPLAY_FIELDS: tuple[str, ...] = (
    "ROE（%）",
    "三年平均 ROE（%）",
    "ROIC（%）",
    "自由现金流净现比",
    "资本支出比率（%）",
    "EPS 五年复合增速（%）",
    "派息率（%）",
    "总市值（亿元）",
    "市盈率（TTM）",
    "市净率",
)

_EMPTY = pd.DataFrame(columns=["code", "name", "roe"])


def _query(connection: sqlite3.Connection, sql: str, params=()) -> pd.DataFrame:
    return pd.read_sql_query(sql, connection, params=params)


def _year_of(period: str) -> str:
    return period[:4]


def _reference_period(connection: sqlite3.Connection, day: str) -> str:
    """筛选日能看到的最新「披露完一轮」的报告期。

    报告季刚开头时当期只有零星几家披露，拿它当基准会把绝大多数股票判成缺数据，
    所以从新到旧找第一个覆盖率不低于上一期 90% 的报告期。
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


def _annual_periods(connection: sqlite3.Connection, day: str, count: int) -> list[str]:
    """最近若干个已披露完整的年报期，从新到旧。"""
    reference = _reference_period(connection, day)
    rows = connection.execute(
        """
        SELECT DISTINCT end_date FROM income
        WHERE report_type = '2' AND end_date <= ? AND end_date LIKE '%1231'
        ORDER BY end_date DESC LIMIT ?
        """,
        (reference, count),
    ).fetchall()
    return [row[0] for row in rows]


def _universe(connection: sqlite3.Connection, day: str) -> pd.DataFrame:
    """股票池：主板/创业板/科创板，排除银行保险和当日停牌股。"""
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


def _annual_totals(
    connection: sqlite3.Connection, table: str, columns: tuple[str, ...], annuals: list[str]
) -> dict[str, pd.DataFrame]:
    """按年报期把单季金额加总；一年不足 4 个季度记 NaN。"""
    quarters = [f"{_year_of(period)}{suffix}" for period in annuals for suffix in _QUARTER_SUFFIXES]
    placeholders = ", ".join("?" * len(quarters))
    frame = _query(
        connection,
        f"SELECT ts_code AS code, end_date, {', '.join(columns)} FROM {table}"
        f" WHERE report_type = '2' AND end_date IN ({placeholders})",
        tuple(quarters),
    )
    totals: dict[str, pd.DataFrame] = {}
    for column in columns:
        matrix = frame.pivot_table(
            index="code", columns="end_date", values=column, aggfunc="last"
        ).reindex(columns=quarters)
        totals[column] = pd.DataFrame(
            {
                _year_of(period): matrix[
                    [f"{_year_of(period)}{suffix}" for suffix in _QUARTER_SUFFIXES]
                ].sum(axis=1, min_count=4)
                for period in annuals
            }
        )
    return totals


def _annual_indicators(
    connection: sqlite3.Connection, columns: tuple[str, ...], annuals: list[str]
) -> dict[str, pd.DataFrame]:
    """年报期的财务指标，行是股票、列是年份。"""
    placeholders = ", ".join("?" * len(annuals))
    frame = _query(
        connection,
        f"SELECT ts_code AS code, end_date, {', '.join(columns)} FROM fina_indicator"
        f" WHERE end_date IN ({placeholders})",
        tuple(annuals),
    )
    return {
        column: frame.pivot_table(index="code", columns="end_date", values=column, aggfunc="last")
        .reindex(columns=annuals)
        .rename(columns=_year_of)
        for column in columns
    }


def _dividend_totals(connection: sqlite3.Connection, years: list[str]) -> pd.DataFrame:
    """每年现金分红总额（元），行是股票、列是年份；没有记录的年份不出现。"""
    frame = _query(
        connection,
        """
        SELECT ts_code AS code, end_date, cash_div_tax, base_share FROM dividend
        WHERE div_proc = '实施' AND end_date BETWEEN ? AND ?
        """,
        (f"{min(years)}0101", f"{max(years)}1231"),
    )
    if frame.empty:
        return pd.DataFrame()
    frame["year"] = frame["end_date"].str[:4]
    # cash_div_tax 是每股税前分红（元），base_share 是万股，两者相乘得万元，再换到元。
    frame["amount"] = frame["cash_div_tax"].fillna(0.0) * frame["base_share"].fillna(0.0) * 10_000.0
    return frame.pivot_table(index="code", columns="year", values="amount", aggfunc="sum")


def _trailing_profit(connection: sqlite3.Connection, reference: str) -> pd.Series:
    """最近 4 个单季的归母净利润之和（元）；缺一个季度就是 NaN。"""
    periods = [
        row[0]
        for row in connection.execute(
            """
            SELECT DISTINCT end_date FROM income
            WHERE report_type = '2' AND end_date <= ?
            ORDER BY end_date DESC LIMIT 4
            """,
            (reference,),
        )
    ]
    if len(periods) < 4:
        return pd.Series(dtype="float64")
    placeholders = ", ".join("?" * len(periods))
    frame = _query(
        connection,
        "SELECT ts_code AS code, end_date, n_income_attr_p AS profit FROM income"
        f" WHERE report_type = '2' AND end_date IN ({placeholders})",
        tuple(periods),
    )
    matrix = frame.pivot_table(
        index="code", columns="end_date", values="profit", aggfunc="last"
    ).reindex(columns=periods)
    return matrix.sum(axis=1, min_count=4)


def _latest_equity(connection: sqlite3.Connection, reference: str) -> pd.Series:
    """最近一期归母股东权益（元），与第 1 集的市净率分母同口径。"""
    period = connection.execute(
        "SELECT MAX(end_date) FROM balancesheet WHERE end_date <= ?", (reference,)
    ).fetchone()[0]
    if period is None:
        return pd.Series(dtype="float64")
    frame = _query(
        connection,
        "SELECT ts_code AS code, total_hldr_eqy_exc_min_int AS equity"
        " FROM balancesheet WHERE end_date = ?",
        (period,),
    )
    return frame.set_index("code")["equity"]


def evaluate(connection: sqlite3.Connection, trade_date: str) -> pd.DataFrame:
    """算出全部通过的股票；任何一项不满足或数据不全的直接不返回。"""
    universe = _universe(connection, trade_date)
    if universe.empty:
        return _EMPTY.copy()

    annuals = _annual_periods(connection, trade_date, YEARS_NEEDED)
    if len(annuals) < YEARS_NEEDED:
        return _EMPTY.copy()
    years = [_year_of(period) for period in annuals]
    newest = years[0]
    oldest = years[-1]

    income = _annual_totals(connection, "income", ("n_income_attr_p", "revenue"), annuals)
    cash = _annual_totals(
        connection, "cashflow", ("n_cashflow_act", "c_pay_acq_const_fiolta"), annuals
    )
    indicators = _annual_indicators(connection, ("roe_waa", "roic", "eps"), annuals)
    dividends = _dividend_totals(connection, years)

    prices = _query(
        connection,
        "SELECT ts_code AS code, total_mv FROM daily_basic WHERE trade_date = ?",
        (trade_date,),
    ).set_index("code")["total_mv"]

    table = universe.set_index("code")
    table["market_cap"] = prices.reindex(table.index)

    reference = _reference_period(connection, trade_date)
    trailing = _trailing_profit(connection, reference).reindex(table.index)
    equity = _latest_equity(connection, reference).reindex(table.index)
    # 只用于展示，不参与任何判定；单位先换算成万元再与总市值相除。
    table["pe_ttm"] = table["market_cap"] / (trailing / 10_000.0)
    table["pb"] = table["market_cap"] / (equity / 10_000.0)

    roe = indicators["roe_waa"].reindex(table.index)
    roic = indicators["roic"].reindex(table.index)
    eps = indicators["eps"].reindex(table.index)

    profit = income["n_income_attr_p"].reindex(table.index)
    revenue = income["revenue"].reindex(table.index)
    operating = cash["n_cashflow_act"].reindex(table.index)
    capex = cash["c_pay_acq_const_fiolta"].reindex(table.index)

    table["roe"] = roe[newest]
    table["roe_average"] = roe[years[:ROE_AVERAGE_YEARS]].mean(axis=1)
    roe_average_ok = roe[years[:ROE_AVERAGE_YEARS]].notna().all(axis=1)
    table["roic"] = roic[newest]

    free_cash_flow = operating[newest] - capex[newest]
    table["free_cash_flow_ratio"] = free_cash_flow / profit[newest]
    table["capex_ratio"] = capex[newest] / revenue[newest]

    table["eps_cagr"] = (eps[newest] / eps[oldest]) ** (1 / (YEARS_NEEDED - 1)) - 1
    eps_ok = eps.notna().all(axis=1) & (eps[newest] > 0) & (eps[oldest] > 0)

    payouts = pd.DataFrame(index=table.index)
    for year in years:
        amount = (
            dividends[year].reindex(table.index).fillna(0.0)
            if year in dividends.columns
            else pd.Series(0.0, index=table.index)
        )
        payouts[year] = amount / profit[year]
        table[f"payout{year}"] = payouts[year]
    table["payout_average"] = payouts[years].mean(axis=1)
    payout_ok = (
        (payouts[years] > PAYOUT_MIN).all(axis=1)
        & profit[years].notna().all(axis=1)
        & (profit[years] > 0).all(axis=1)
    )

    passed = table[
        roe_average_ok
        & (table["roe"] > ROE_MIN)
        & (table["roe_average"] > ROE_AVERAGE_MIN)
        & (table["roic"] > ROIC_MIN)
        & (table["roic"] <= ROIC_MAX)
        & (profit[newest] > 0)
        & (table["free_cash_flow_ratio"] > FREE_CASH_FLOW_RATIO_MIN)
        & (revenue[newest] > 0)
        & (table["capex_ratio"] < CAPEX_RATIO_MAX)
        & eps_ok
        & (table["eps_cagr"] > EPS_CAGR_MIN)
        & payout_ok
        & (table["market_cap"] > MARKET_CAP_MIN_WAN)
    ].dropna(
        subset=["roe", "roic", "free_cash_flow_ratio", "capex_ratio", "eps_cagr", "market_cap"]
    )

    return passed.sort_values(
        ["roe_average", "code"], ascending=[False, True], kind="stable"
    ).reset_index()


def _percent(value: float) -> str:
    return f"{value * 100:.2f}"


def _optional(value) -> str:
    """参考列：没有正值就写「—」，不参与判定，也不按 0 或负数展示。"""
    return "—" if pd.isna(value) or value <= 0 else f"{value:.2f}"


def _sort_key(value) -> float | None:
    """参考列的排序键：显示「—」的那些不给排序值，免得拿负数排出假顺序。"""
    return None if pd.isna(value) or value <= 0 else float(value)


def screen(connection: sqlite3.Connection, trade_date: str) -> pd.DataFrame:
    """筛选并按展示口径格式化，返回给报告用的行。"""
    passed = evaluate(connection, trade_date)
    if passed.empty:
        return passed

    annuals = _annual_periods(connection, trade_date, YEARS_NEEDED)
    years = [_year_of(period) for period in annuals]

    rows = []
    sort_values: dict[str, list] = {field: [] for field in DISPLAY_FIELDS}
    for row in passed.itertuples(index=False):
        payouts = [getattr(row, f"payout{year}") for year in years]
        rows.append(
            {
                "code": row.code,
                "name": row.name,
                "ROE（%）": f"{row.roe:.2f}",
                "三年平均 ROE（%）": f"{row.roe_average:.2f}",
                "ROIC（%）": f"{row.roic:.2f}",
                "自由现金流净现比": f"{row.free_cash_flow_ratio:.2f}",
                "资本支出比率（%）": _percent(row.capex_ratio),
                "EPS 五年复合增速（%）": _percent(row.eps_cagr),
                "派息率（%）": " / ".join(_percent(value) for value in payouts),
                "总市值（亿元）": f"{row.market_cap / 10000:.2f}",
                "市盈率（TTM）": _optional(row.pe_ttm),
                "市净率": _optional(row.pb),
            }
        )
        sort_values["ROE（%）"].append(float(row.roe))
        sort_values["三年平均 ROE（%）"].append(float(row.roe_average))
        sort_values["ROIC（%）"].append(float(row.roic))
        sort_values["自由现金流净现比"].append(float(row.free_cash_flow_ratio))
        sort_values["资本支出比率（%）"].append(float(row.capex_ratio) * 100)
        sort_values["EPS 五年复合增速（%）"].append(float(row.eps_cagr) * 100)
        sort_values["派息率（%）"].append(float(row.payout_average))
        sort_values["总市值（亿元）"].append(float(row.market_cap) / 10000)
        sort_values["市盈率（TTM）"].append(_sort_key(row.pe_ttm))
        sort_values["市净率"].append(_sort_key(row.pb))

    frame = pd.DataFrame(rows)
    frame.attrs["note"] = (
        f"ROE 取 {years[0]} 年报；三年平均 ROE 是 {' / '.join(years[:ROE_AVERAGE_YEARS])} 的均值；"
        f"派息率是 {' / '.join(years)} 每年，按新到旧排列；点列头排序时派息率按五年均值比；"
        "市盈率、市净率只作参考，不参与筛选"
    )
    frame.attrs["sort_values"] = sort_values
    return frame


class BuffettStrategy(Strategy):
    spec = StrategySpec(
        strategy_id="episode-04-buffett",
        menu_name="巴菲特（可用）",
        output_dir="episode-04-buffett",
        required_datasets=(
            "股票列表",
            "交易日历",
            "停复牌",
            "每日指标",
            "利润表",
            "资产负债表",
            "财务指标",
            "分红送配",
            "现金流量表",
        ),
        display_fields=DISPLAY_FIELDS,
    )

    def screen(self, connection: sqlite3.Connection, trade_date: str) -> pd.DataFrame:
        return screen(connection, trade_date)
