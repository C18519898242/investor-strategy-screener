"""现金流量表（`cashflow`）。

只存**单季口径**（`report_type=2`），与 `income` 一致：策略按季度算，累计值可以由四个
单季加出来，两种口径都存会让下游每次先判断用哪一种。2026-10-24 实测：600519 与
000001 的 2021–2024 四个年报，`n_cashflow_act` 和 `c_pay_acq_const_fiolta` 用四季合计
与累计值相比偏差 0.0000%。

两个字段上的坑（实测）
    `free_cashflow` 上游恒为空（3 只股票 275 行全空），自由现金流要自己算：
    `n_cashflow_act - c_pay_acq_const_fiolta`。`net_profit` 在单季口径下也基本为空
    （270,268 行里只有 7,714 行有值），净利润从 `income` 表取。

为什么走直连端点而不是 SDK 客户端
    生产 SDK 代理（`TUSHARE_API_URL`）对 `cashflow` 一律返回
    `[upstream_pool_exhausted] 数据当前不可用`，同一时刻 `income`、`balancesheet` 都能
    正常返回，说明不是整体故障。直连端点没有这个问题——回填脚本用它拉了 27 万行。
"""

import json
import logging
import sqlite3
import urllib.error
import urllib.request
from datetime import date

from screener.config import load_settings
from screener.store import insert_rows
from screener.sync.errors import SyncError
from screener.sync.report_periods import target_periods
from screener.sync.retry import call_with_retry

logger = logging.getLogger(__name__)

DATASET = "现金流量表"

REPORT_TYPE = "2"

ENDPOINT = "https://datahubco.com/api/tushare"
TIMEOUT_SECONDS = 300

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


def _fetch_period(ts_code: str, period: str) -> list[dict]:
    """取一只股票一个报告期的单季现金流量表。"""
    token = load_settings().tushare_token.strip()
    body = json.dumps(
        {
            "api_name": "cashflow",
            "token": token,
            "params": {"ts_code": ts_code, "period": period, "report_type": REPORT_TYPE},
            "fields": ",".join(COLUMNS),
        }
    ).encode()
    request = urllib.request.Request(
        f"{ENDPOINT}/cashflow", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read().decode() or "{}")
    if payload.get("code") != 0:
        code = payload.get("code")
        message = str(payload.get("msg") or "")
        if code == 429 or "concurrent" in message:
            raise SyncError(f"rate_limited：{message}")
        raise SyncError(f"code={code} {message}")
    data = payload["data"]
    fields = data["fields"]
    return [dict(zip(fields, item, strict=False)) for item in data["items"]]


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


def _store(connection: sqlite3.Connection, rows: list[dict], period: str) -> int:
    """把一次返回写进表，只留目标报告期的行；同一版本号只留最后一条。"""
    picked: dict[tuple, tuple] = {}
    for row in rows:
        if row.get("end_date") != period:
            continue
        picked[tuple(row.get(name) for name in KEY)] = tuple(row.get(name) for name in COLUMNS)
    if not picked:
        return 0
    return insert_rows(
        connection,
        table="cashflow",
        columns=COLUMNS,
        rows=picked.values(),
    )


def fetch_cashflow(
    connection: sqlite3.Connection, client: object = None, *, today: date | None = None
) -> int:
    """按报告期补齐现金流量表：只请求本地缺的股票，每只一次调用。

    `client` 是登记表统一传进来的 SDK 客户端，这个数据集用不上——见模块说明。
    """
    day = today or date.today()
    written = 0
    for period in target_periods(day):
        codes = _codes_missing(connection, period)
        total = len(codes)
        logger.info("%s %s：本地缺 %d 只", DATASET, period, total)
        for index, ts_code in enumerate(codes, start=1):
            logger.info("%s %s 第 %d/%d 只：%s", DATASET, period, index, total, ts_code)
            rows = call_with_retry(
                lambda ts_code=ts_code, period=period: _fetch_period(ts_code, period)
            )
            written += _store(connection, rows, period)

    return written
