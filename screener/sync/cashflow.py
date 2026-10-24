"""现金流量表（`cashflow`）。

只存**单季口径**（`report_type=2`），与 `income` 一致：策略按季度算，累计值可以由四个
单季加出来，两种口径都存会让下游每次先判断用哪一种。2026-10-24 实测：600519 与
000001 的 2021–2024 四个年报，`n_cashflow_act` 和 `c_pay_acq_const_fiolta` 用四季合计
与累计值相比偏差 0.0000%。

**`free_cashflow` 这一列上游恒为空**（实测 3 只股票 275 行全空），所以自由现金流要自己
算：`n_cashflow_act - c_pay_acq_const_fiolta`。单季口径下 `n_income` 同样全空，净利润
从 `income` 表取。
"""

import logging
import sqlite3
from datetime import date

from screener.store import insert_rows
from screener.sync.report_periods import target_periods
from screener.sync.retry import call_with_retry

logger = logging.getLogger(__name__)

DATASET = "现金流量表"

REPORT_TYPE = "2"

COLUMNS: tuple[str, ...] = (
    "ts_code",
    "ann_date",
    "f_ann_date",
    "end_date",
    "comp_type",
    "report_type",
    "end_type",
    "net_profit",
    "finan_exp",
    "c_fr_sale_sg",
    "recp_tax_rends",
    "n_depos_incr_fi",
    "n_incr_loans_cb",
    "n_inc_borr_oth_fi",
    "prem_fr_orig_contr",
    "n_incr_insured_dep",
    "n_reinsur_prem",
    "n_incr_disp_tfa",
    "ifc_cash_incr",
    "n_incr_disp_faas",
    "n_incr_loans_oth_bank",
    "n_cap_incr_repur",
    "c_fr_oth_operate_a",
    "c_inf_fr_operate_a",
    "c_paid_goods_s",
    "c_paid_to_for_empl",
    "c_paid_for_taxes",
    "n_incr_clt_loan_adv",
    "n_incr_dep_cbob",
    "c_pay_claims_orig_inco",
    "pay_handling_chrg",
    "pay_comm_insur_plcy",
    "oth_cash_pay_oper_act",
    "st_cash_out_act",
    "n_cashflow_act",
    "oth_recp_ral_inv_act",
    "c_disp_withdrwl_invest",
    "c_recp_return_invest",
    "n_recp_disp_fiolta",
    "n_recp_disp_sobu",
    "stot_inflows_inv_act",
    "c_pay_acq_const_fiolta",
    "c_paid_invest",
    "n_disp_subs_oth_biz",
    "oth_pay_ral_inv_act",
    "n_incr_pledge_loan",
    "stot_out_inv_act",
    "n_cashflow_inv_act",
    "c_recp_borrow",
    "proc_issue_bonds",
    "oth_cash_recp_ral_fnc_act",
    "stot_cash_in_fnc_act",
    "free_cashflow",
    "c_prepay_amt_borr",
    "c_pay_dist_dpcp_int_exp",
    "incl_dvd_profit_paid_sc_ms",
    "oth_cashpay_ral_fnc_act",
    "stot_cashout_fnc_act",
    "n_cash_flows_fnc_act",
    "eff_fx_flu_cash",
    "n_incr_cash_cash_equ",
    "c_cash_equ_beg_period",
    "c_cash_equ_end_period",
    "c_recp_cap_contrib",
    "incl_cash_rec_saims",
    "uncon_invest_loss",
    "prov_depr_assets",
    "depr_fa_coga_dpba",
    "amort_intang_assets",
    "lt_amort_deferred_exp",
    "decr_deferred_exp",
    "incr_acc_exp",
    "loss_disp_fiolta",
    "loss_scr_fa",
    "loss_fv_chg",
    "invest_loss",
    "decr_def_inc_tax_assets",
    "incr_def_inc_tax_liab",
    "decr_inventories",
    "decr_oper_payable",
    "incr_oper_payable",
    "others",
    "im_net_cashflow_oper_act",
    "conv_debt_into_cap",
    "conv_copbonds_due_within_1y",
    "fa_fnc_leases",
    "im_n_incr_cash_equ",
    "net_dism_capital_add",
    "net_cash_rece_sec",
    "credit_impa_loss",
    "use_right_asset_dep",
    "oth_loss_asset",
    "end_bal_cash",
    "beg_bal_cash",
    "end_bal_cash_equ",
    "beg_bal_cash_equ",
    "update_flag",
)

# 一行财报的唯一标识，与建表时的主键一致。同一期会有更正公告，`update_flag` 区分版本。
KEY: tuple[str, ...] = ("ts_code", "end_date", "report_type", "ann_date", "update_flag")


def _codes_missing(connection: sqlite3.Connection, period: str) -> list[str]:
    """本地还没有这一期单季数据的在市股票。

    判定粒度是「股票 + 报告期 + 口径」三列，不是只看报告期。
    """
    return [
        row[0]
        for row in connection.execute(
            """
            SELECT ts_code FROM stock_basic
            WHERE ts_code NOT IN (
                SELECT ts_code FROM cashflow WHERE end_date = ? AND report_type = ?
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
        table="cashflow",
        columns=COLUMNS,
        rows=rows.itertuples(index=False, name=None),
    )


def fetch_cashflow(
    connection: sqlite3.Connection, client: object, *, today: date | None = None
) -> int:
    """按报告期补齐现金流量表：只请求本地缺的股票，每只一次调用。"""
    day = today or date.today()
    written = 0
    for period in target_periods(day):
        codes = _codes_missing(connection, period)
        total = len(codes)
        logger.info("现金流量表 %s：本地缺 %d 只", period, total)
        for index, ts_code in enumerate(codes, start=1):
            logger.info("现金流量表 %s 第 %d/%d 只：%s", period, index, total, ts_code)
            frame = call_with_retry(
                lambda ts_code=ts_code, period=period: client.cashflow(
                    ts_code=ts_code,
                    period=period,
                    report_type=REPORT_TYPE,
                    fields=",".join(COLUMNS),
                )
            )
            written += _store(connection, frame, period)

    return written
