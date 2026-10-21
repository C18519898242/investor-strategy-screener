import logging
import sqlite3
from datetime import date

from screener.store import insert_rows
from screener.sync.report_periods import target_periods
from screener.sync.retry import call_with_retry

logger = logging.getLogger(__name__)

DATASET = "资产负债表"

# 上游对 balancesheet 只返回合并报表（report_type='1'），没有单季/母公司版本可以挑。
REPORT_TYPE = "1"

COLUMNS: tuple[str, ...] = (
    "ts_code",
    "ann_date",
    "f_ann_date",
    "end_date",
    "report_type",
    "comp_type",
    "end_type",
    "total_share",
    "cap_rese",
    "undistr_porfit",
    "surplus_rese",
    "special_rese",
    "money_cap",
    "trad_asset",
    "notes_receiv",
    "accounts_receiv",
    "oth_receiv",
    "prepayment",
    "div_receiv",
    "int_receiv",
    "inventories",
    "amor_exp",
    "nca_within_1y",
    "sett_rsrv",
    "loanto_oth_bank_fi",
    "premium_receiv",
    "reinsur_receiv",
    "reinsur_res_receiv",
    "pur_resale_fa",
    "oth_cur_assets",
    "total_cur_assets",
    "fa_avail_for_sale",
    "htm_invest",
    "lt_eqt_invest",
    "invest_real_estate",
    "time_deposits",
    "oth_assets",
    "lt_rec",
    "fix_assets",
    "cip",
    "const_materials",
    "fixed_assets_disp",
    "produc_bio_assets",
    "oil_and_gas_assets",
    "intan_assets",
    "r_and_d",
    "goodwill",
    "lt_amor_exp",
    "defer_tax_assets",
    "decr_in_disbur",
    "oth_nca",
    "total_nca",
    "cash_reser_cb",
    "depos_in_oth_bfi",
    "prec_metals",
    "deriv_assets",
    "rr_reins_une_prem",
    "rr_reins_outstd_cla",
    "rr_reins_lins_liab",
    "rr_reins_lthins_liab",
    "refund_depos",
    "ph_pledge_loans",
    "refund_cap_depos",
    "indep_acct_assets",
    "client_depos",
    "client_prov",
    "transac_seat_fee",
    "invest_as_receiv",
    "total_assets",
    "lt_borr",
    "st_borr",
    "cb_borr",
    "depos_ib_deposits",
    "loan_oth_bank",
    "trading_fl",
    "notes_payable",
    "acct_payable",
    "adv_receipts",
    "sold_for_repur_fa",
    "comm_payable",
    "payroll_payable",
    "taxes_payable",
    "int_payable",
    "div_payable",
    "oth_payable",
    "acc_exp",
    "deferred_inc",
    "st_bonds_payable",
    "payable_to_reinsurer",
    "rsrv_insur_cont",
    "acting_trading_sec",
    "acting_uw_sec",
    "non_cur_liab_due_1y",
    "oth_cur_liab",
    "total_cur_liab",
    "bond_payable",
    "lt_payable",
    "specific_payables",
    "estimated_liab",
    "defer_tax_liab",
    "defer_inc_non_cur_liab",
    "oth_ncl",
    "depos_oth_bfi",
    "deriv_liab",
    "depos",
    "agency_bus_liab",
    "oth_liab",
    "prem_receiv_adva",
    "depos_received",
    "ph_invest",
    "reser_une_prem",
    "reser_outstd_claims",
    "reser_lins_liab",
    "reser_lthins_liab",
    "indept_acc_liab",
    "pledge_borr",
    "indem_payable",
    "policy_div_payable",
    "total_liab",
    "treasury_share",
    "ordin_risk_reser",
    "forex_differ",
    "invest_loss_unconf",
    "minority_int",
    "total_hldr_eqy_exc_min_int",
    "total_hldr_eqy_inc_min_int",
    "total_liab_hldr_eqy",
    "lt_payroll_payable",
    "oth_comp_income",
    "oth_eqt_tools",
    "oth_eqt_tools_p_shr",
    "lending_funds",
    "acc_receivable",
    "st_fin_payable",
    "payables",
    "hfs_assets",
    "hfs_sales",
    "cost_fin_assets",
    "fair_value_fin_assets",
    "cip_total",
    "oth_pay_total",
    "long_pay_total",
    "debt_invest",
    "oth_debt_invest",
    "oth_eq_invest",
    "oth_illiq_fin_assets",
    "oth_eq_ppbond",
    "receiv_financing",
    "use_right_assets",
    "lease_liab",
    "contract_assets",
    "contract_liab",
    "accounts_receiv_bill",
    "accounts_pay",
    "oth_rcv_total",
    "fix_assets_total",
    "update_flag",
)

# 一个报告期只存一行，主键据此设计。上游对同一期会返回多条：修正公告、update_flag
# 0/1 成对行、以及公告日不变但实际公告日推后的重述。多版本留给策略只会让每次查询
# 都要先判断用哪一条（2026-10-20 决定）。
KEY: tuple[str, ...] = ("ts_code", "end_date", "report_type")

# 判断"哪一条最新"的依据，越靠后优先级越高。f_ann_date 是实际公告日，重述时它变、
# ann_date 可能不变；update_flag 只用来在两者都相同时定序。
VERSION_COLUMNS: tuple[str, ...] = ("f_ann_date", "ann_date", "update_flag")

_INDEX = {name: position for position, name in enumerate(COLUMNS)}
_KEY_POSITIONS = tuple(_INDEX[name] for name in KEY)
_VERSION_POSITIONS = tuple(_INDEX[name] for name in VERSION_COLUMNS)


def keep_latest(rows: list[tuple]) -> list[tuple]:
    """同一`KEY`只留一行，取 `VERSION_COLUMNS` 最大的那条。

    三者完全相同还出现两行时（实测没有，但上游没保证），取响应里靠后的一条，
    和 income 的 `drop_duplicates(keep="last")` 保持一致。
    """
    best: dict[tuple, tuple[str, ...]] = {}
    kept: dict[tuple, tuple] = {}
    for row in rows:
        key = tuple(row[position] for position in _KEY_POSITIONS)
        version = tuple(str(row[position] or "") for position in _VERSION_POSITIONS)
        if key not in best or version >= best[key]:
            best[key] = version
            kept[key] = row
    return list(kept.values())


def _codes_missing(connection: sqlite3.Connection, period: str) -> list[str]:
    """本地还没有这一期数据的在市股票。"""
    return [
        row[0]
        for row in connection.execute(
            """
            SELECT ts_code FROM stock_basic
            WHERE ts_code NOT IN (
                SELECT ts_code FROM balancesheet WHERE end_date = ? AND report_type = ?
            )
            ORDER BY ts_code
            """,
            (period, REPORT_TYPE),
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
        table="balancesheet",
        columns=COLUMNS,
        rows=keep_latest(records),
    )


def fetch_balancesheet(
    connection: sqlite3.Connection, client: object, *, today: date | None = None
) -> int:
    """按报告期补齐资产负债表：只请求本地缺的股票，每只一次调用。"""
    day = today or date.today()
    written = 0
    for period in target_periods(day):
        codes = _codes_missing(connection, period)
        total = len(codes)
        logger.info("资产负债表 %s：本地缺 %d 只", period, total)
        for index, ts_code in enumerate(codes, start=1):
            logger.info("资产负债表 %s 第 %d/%d 只：%s", period, index, total, ts_code)
            frame = call_with_retry(
                lambda ts_code=ts_code, period=period: client.balancesheet(
                    ts_code=ts_code,
                    period=period,
                    report_type=REPORT_TYPE,
                    fields=",".join(COLUMNS),
                )
            )
            written += _store(connection, frame, period)

    return written
