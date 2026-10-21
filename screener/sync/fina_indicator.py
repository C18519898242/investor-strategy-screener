import logging
import sqlite3
from datetime import date

from screener.store import insert_rows
from screener.sync.report_periods import target_periods
from screener.sync.retry import call_with_retry
from screener.sync.versions import keep_latest as _keep_latest

logger = logging.getLogger(__name__)

DATASET = "财务指标"

# 上游 `fina_indicator` 的完整字段表（109 列，2026-10-21 实测）。
# 注意：不传 fields 时逐股路径只返回 108 列、缺 update_flag；`fina_indicator_vip`
# 的默认值才含它。所以必须显式列全，不能省。
COLUMNS: tuple[str, ...] = (
    "ts_code",
    "ann_date",
    "end_date",
    "eps",
    "dt_eps",
    "total_revenue_ps",
    "revenue_ps",
    "capital_rese_ps",
    "surplus_rese_ps",
    "undist_profit_ps",
    "extra_item",
    "profit_dedt",
    "gross_margin",
    "current_ratio",
    "quick_ratio",
    "cash_ratio",
    "ar_turn",
    "ca_turn",
    "fa_turn",
    "assets_turn",
    "op_income",
    "ebit",
    "ebitda",
    "fcff",
    "fcfe",
    "current_exint",
    "noncurrent_exint",
    "interestdebt",
    "netdebt",
    "tangible_asset",
    "working_capital",
    "networking_capital",
    "invest_capital",
    "retained_earnings",
    "diluted2_eps",
    "bps",
    "ocfps",
    "retainedps",
    "cfps",
    "ebit_ps",
    "fcff_ps",
    "fcfe_ps",
    "netprofit_margin",
    "grossprofit_margin",
    "cogs_of_sales",
    "expense_of_sales",
    "profit_to_gr",
    "saleexp_to_gr",
    "adminexp_of_gr",
    "finaexp_of_gr",
    "impai_ttm",
    "gc_of_gr",
    "op_of_gr",
    "ebit_of_gr",
    "roe",
    "roe_waa",
    "roe_dt",
    "roa",
    "npta",
    "roic",
    "roe_yearly",
    "roa2_yearly",
    "debt_to_assets",
    "assets_to_eqt",
    "dp_assets_to_eqt",
    "ca_to_assets",
    "nca_to_assets",
    "tbassets_to_totalassets",
    "int_to_talcap",
    "eqt_to_talcapital",
    "currentdebt_to_debt",
    "longdeb_to_debt",
    "ocf_to_shortdebt",
    "debt_to_eqt",
    "eqt_to_debt",
    "eqt_to_interestdebt",
    "tangibleasset_to_debt",
    "tangasset_to_intdebt",
    "tangibleasset_to_netdebt",
    "ocf_to_debt",
    "turn_days",
    "roa_yearly",
    "roa_dp",
    "fixed_assets",
    "profit_to_op",
    "q_saleexp_to_gr",
    "q_gc_to_gr",
    "q_roe",
    "q_dt_roe",
    "q_npta",
    "q_ocf_to_sales",
    "basic_eps_yoy",
    "dt_eps_yoy",
    "cfps_yoy",
    "op_yoy",
    "ebt_yoy",
    "netprofit_yoy",
    "dt_netprofit_yoy",
    "ocf_yoy",
    "roe_yoy",
    "bps_yoy",
    "assets_yoy",
    "eqt_yoy",
    "tr_yoy",
    "or_yoy",
    "q_sales_yoy",
    "q_op_qoq",
    "equity_yoy",
    "update_flag",
)

# 该接口没有 report_type / comp_type / f_ann_date，一个报告期只可能有一条口径；
# 但同一报告期会返回修订版本，所以按"一个季度一行"入库（2026-10-21 决定）。
KEY: tuple[str, ...] = ("ts_code", "end_date")

# 判断"哪一条最新"的依据。实测多版本全部是同报告期、`update_flag` 0/1 成对；
# 公告日不同时按公告日，公告日相同再按 update_flag 定序。
VERSION_COLUMNS: tuple[str, ...] = ("ann_date", "update_flag")

_INDEX = {name: position for position, name in enumerate(COLUMNS)}


def keep_latest(rows: list[tuple]) -> list[tuple]:
    """同一`KEY`只留一行，取 `VERSION_COLUMNS` 最大的那条。"""
    return _keep_latest(rows, columns=COLUMNS, key=KEY, version=VERSION_COLUMNS)


def _codes_missing(connection: sqlite3.Connection, period: str) -> list[str]:
    """本地还没有这一期数据的在市股票。"""
    return [
        row[0]
        for row in connection.execute(
            """
            SELECT ts_code FROM stock_basic
            WHERE ts_code NOT IN (
                SELECT ts_code FROM fina_indicator WHERE end_date = ?
            )
            ORDER BY ts_code
            """,
            (period,),
        )
    ]


def _store(connection: sqlite3.Connection, frame, period: str) -> int:
    """把一次返回写进表，只留目标报告期、且每期一行。"""
    if frame is None or frame.empty:
        return 0
    selected = frame.reindex(columns=list(COLUMNS))
    selected = selected[selected["end_date"] == period]
    if selected.empty:
        return 0
    rows = selected.astype(object).where(selected.notna(), None)
    records = [tuple(row) for row in rows.itertuples(index=False, name=None)]
    return insert_rows(
        connection,
        table="fina_indicator",
        columns=COLUMNS,
        rows=keep_latest(records),
    )


def fetch_fina_indicator(
    connection: sqlite3.Connection, client: object, *, today: date | None = None
) -> int:
    """按报告期补齐财务指标：只请求本地缺的股票，每只一次调用。"""
    day = today or date.today()
    written = 0
    for period in target_periods(day):
        codes = _codes_missing(connection, period)
        total = len(codes)
        logger.info("财务指标 %s：本地缺 %d 只", period, total)
        for index, ts_code in enumerate(codes, start=1):
            logger.info("财务指标 %s 第 %d/%d 只：%s", period, index, total, ts_code)
            frame = call_with_retry(
                lambda ts_code=ts_code, period=period: client.fina_indicator(
                    ts_code=ts_code,
                    period=period,
                    fields=",".join(COLUMNS),
                )
            )
            written += _store(connection, frame, period)

    return written
