import logging
import sqlite3
from datetime import date

from screener.store import insert_rows
from screener.sync.retry import call_with_retry

logger = logging.getLogger(__name__)

DATASET = "利润表"

# 只存单季口径。策略按季度算，不需要累计值；两种口径都存会让下游每次先判断用哪一种。
# 上游一次调用只返回一种口径，单季必须显式指定 report_type='2'（2026-10-20 实测）。
REPORT_TYPE = "2"

COLUMNS: tuple[str, ...] = (
    "ts_code",
    "ann_date",
    "f_ann_date",
    "end_date",
    "report_type",
    "comp_type",
    "end_type",
    "basic_eps",
    "diluted_eps",
    "total_revenue",
    "revenue",
    "int_income",
    "prem_earned",
    "comm_income",
    "n_commis_income",
    "n_oth_income",
    "n_oth_b_income",
    "prem_income",
    "out_prem",
    "une_prem_reser",
    "reins_income",
    "n_sec_tb_income",
    "n_sec_uw_income",
    "n_asset_mg_income",
    "oth_b_income",
    "fv_value_chg_gain",
    "invest_income",
    "ass_invest_income",
    "forex_gain",
    "total_cogs",
    "oper_cost",
    "int_exp",
    "comm_exp",
    "biz_tax_surchg",
    "sell_exp",
    "admin_exp",
    "fin_exp",
    "assets_impair_loss",
    "prem_refund",
    "compens_payout",
    "reser_insur_liab",
    "div_payt",
    "reins_exp",
    "oper_exp",
    "compens_payout_refu",
    "insur_reser_refu",
    "reins_cost_refund",
    "other_bus_cost",
    "operate_profit",
    "non_oper_income",
    "non_oper_exp",
    "nca_disploss",
    "total_profit",
    "income_tax",
    "n_income",
    "n_income_attr_p",
    "minority_gain",
    "oth_compr_income",
    "t_compr_income",
    "compr_inc_attr_p",
    "compr_inc_attr_m_s",
    "ebit",
    "ebitda",
    "insurance_exp",
    "undist_profit",
    "distable_profit",
    "rd_exp",
    "fin_exp_int_exp",
    "fin_exp_int_inc",
    "transfer_surplus_rese",
    "transfer_housing_imprest",
    "transfer_oth",
    "adj_lossgain",
    "withdra_legal_surplus",
    "withdra_legal_pubfund",
    "withdra_biz_devfund",
    "withdra_rese_fund",
    "withdra_oth_ersu",
    "workers_welfare",
    "distr_profit_shrhder",
    "prfshare_payable_dvd",
    "comshare_payable_dvd",
    "capit_comstock_div",
    "net_after_nr_lp_correct",
    "credit_impa_loss",
    "net_expo_hedging_benefits",
    "oth_impair_loss_assets",
    "total_opcost",
    "amodcost_fin_assets",
    "oth_income",
    "asset_disp_income",
    "continued_net_profit",
    "end_net_profit",
    "update_flag",
)

# 一行财报的唯一标识，与建表时的主键一致。上游对同一期可能返回多个修订版本。
KEY: tuple[str, ...] = ("ts_code", "end_date", "report_type", "ann_date", "update_flag")


def target_periods(today: date) -> tuple[str, ...]:
    """最近一个已经过了法定披露截止日的报告期。

    按截止日算而不是按自然季度算：一季报的截止日是 4 月 30 日，按自然季度会在
    1～3 月去拉一个还没人披露的 0331。4 月 30 日同时是上年年报的截止日，所以从
    那天起两期一起补。
    """
    if (today.month, today.day) >= (10, 31):
        return (f"{today.year}0930",)
    if (today.month, today.day) >= (8, 31):
        return (f"{today.year}0630",)
    if (today.month, today.day) >= (4, 30):
        return (f"{today.year}0331", f"{today.year - 1}1231")
    return (f"{today.year - 1}0930",)


def _codes_missing(connection: sqlite3.Connection, period: str) -> list[str]:
    """本地还没有这一期单季数据的在市股票。

    判定粒度是「股票 + 报告期 + 口径」三列，不是只看报告期：同一期另一口径的行
    存在，不代表这一口径也有。已经有的股票不发请求，晚到的修订也不再追。
    """
    return [
        row[0]
        for row in connection.execute(
            """
            SELECT ts_code FROM stock_basic
            WHERE ts_code NOT IN (
                SELECT ts_code FROM income WHERE end_date = ? AND report_type = ?
            )
            ORDER BY ts_code
            """,
            (period, REPORT_TYPE),
        )
    ]


def _store(connection: sqlite3.Connection, frame, period: str) -> int:
    """把一次返回写进表，只留目标报告期的行。"""
    if frame is None or frame.empty:
        return 0
    selected = frame.reindex(columns=list(COLUMNS))
    selected = selected[selected["end_date"] == period]
    if selected.empty:
        return 0
    selected = selected.drop_duplicates(subset=list(KEY), keep="last")
    rows = selected.astype(object).where(selected.notna(), None)
    return insert_rows(
        connection,
        table="income",
        columns=COLUMNS,
        rows=rows.itertuples(index=False, name=None),
    )


def fetch_income(
    connection: sqlite3.Connection, client: object, *, today: date | None = None
) -> int:
    """按报告期补齐利润表：只请求本地缺的股票，每只一次调用。

    首次补某个新报告期是全市场一遍（约 5,500 次调用）；之后同一期再跑是 0 次。
    """
    day = today or date.today()
    written = 0
    for period in target_periods(day):
        codes = _codes_missing(connection, period)
        total = len(codes)
        logger.info("利润表 %s：本地缺 %d 只", period, total)
        for index, ts_code in enumerate(codes, start=1):
            logger.info("利润表 %s 第 %d/%d 只：%s", period, index, total, ts_code)
            frame = call_with_retry(
                lambda ts_code=ts_code, period=period: client.income(
                    ts_code=ts_code,
                    period=period,
                    report_type=REPORT_TYPE,
                    fields=",".join(COLUMNS),
                )
            )
            written += _store(connection, frame, period)

    return written
