"""第 1 集研究用探针（第九轮）：财报三表能否按公告日增量取，以及同一报告期是否真有多版本。

用途：为数据建模提案 D8（同步进度与差集）与 D3（多版本保留）提供实测依据。
只读，不写任何文件。代理端限制见 docs/tushare-api-evaluation.md 第 6 节。
"""

import sys
import time
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "screener").is_dir())
sys.path.insert(0, str(ROOT))

import tushare as ts

from screener.config import load_settings

cfg = load_settings()
pro = ts.pro_api(cfg.tushare_token, timeout=120)
pro._DataApi__http_url = cfg.tushare_api_url

INCOME_FIELDS = (
    "ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,"
    "n_income_attr_p,total_revenue,revenue,basic_eps"
)
BALANCE_FIELDS = (
    "ts_code,ann_date,end_date,report_type,update_flag,"
    "total_cur_assets,total_cur_liab,total_ncl,total_hldr_eqy_exc_min_int,goodwill"
)
FINA_FIELDS = "ts_code,ann_date,end_date,update_flag,roe_waa,roe,roe_dt,debt_to_assets,roic,eps"


def call(label, api, tries=2, pause=6, **params):
    for attempt in range(1, tries + 1):
        try:
            frame = getattr(pro, api)(**params)
            time.sleep(pause)
            rows = 0 if frame is None else len(frame)
            print(f"[OK   ] {label:<44} -> {rows} 行")
            return frame
        except Exception as error:  # noqa: BLE001
            if attempt == tries:
                print(f"[FAIL ] {label:<44} -> {type(error).__name__}: {str(error)[:90]}")
                return None
            time.sleep(20)
    return None


def duplicates(frame, keys, label):
    """同一组键出现多行 = 该报告期存在多个版本。"""
    if frame is None or not len(frame):
        print(f"        {label}: 无数据，跳过重复检查")
        return
    counts = frame.groupby(keys).size()
    multi = counts[counts > 1]
    print(f"        {label}: 唯一键 {len(counts)} 组，出现多行的 {len(multi)} 组")
    if len(multi):
        print(f"        最多的一组有 {int(multi.max())} 行，样例: {multi.head(3).to_dict()}")
        sample_key = multi.index[0]
        mask = frame.set_index(keys).index.isin([sample_key])
        print(frame[mask].to_string(index=False, max_colwidth=24))


print("=== A) 财报三表能否按 ann_date 取（单日全市场）===")
income_day = None
for day in ("20260430", "20260429", "20260428", "20260425"):
    frame = call(f"income ann_date={day}", "income", ann_date=day, fields=INCOME_FIELDS)
    if frame is not None and len(frame):
        income_day = frame
        print(f"        使用公告日 {day} 作为后续样本")
        break
    print(f"        公告日 {day} 无数据，换下一个")

balance_day = call("balancesheet ann_date=20260430", "balancesheet", ann_date="20260430", fields=BALANCE_FIELDS)
fina_day = call("fina_indicator ann_date=20260430", "fina_indicator", ann_date="20260430", fields=FINA_FIELDS)

print("\n=== B) 按公告日的区间上限（决定一次更新要几次调用）===")
call(
    "income 30 天区间 20260401-20260430",
    "income",
    start_date="20260401",
    end_date="20260430",
    fields=INCOME_FIELDS,
)
call(
    "income 90 天区间 20260101-20260331",
    "income",
    start_date="20260101",
    end_date="20260331",
    fields=INCOME_FIELDS,
)
call(
    "income 365 天区间 20260101-20261231",
    "income",
    start_date="20260101",
    end_date="20261231",
    fields=INCOME_FIELDS,
)

print("\n=== C) 同一报告期是否出现多行（D3 的关键验证）===")
duplicates(income_day, ["ts_code", "end_date", "report_type"], "income 单公告日")
duplicates(balance_day, ["ts_code", "end_date", "report_type"], "balancesheet 单公告日")
duplicates(fina_day, ["ts_code", "end_date"], "fina_indicator 单公告日")

print("\n=== D) 单只股票的多年区间：重复行与 update_flag 取值 ===")
one = call(
    "600519.SH 20240101-20261231",
    "income",
    ts_code="600519.SH",
    start_date="20240101",
    end_date="20261231",
    fields=INCOME_FIELDS,
)
if one is not None and len(one):
    print(one.to_string(index=False, max_colwidth=20))
    duplicates(one, ["ts_code", "end_date", "report_type"], "600519 多年区间")
    print(f"        update_flag 取值分布: {one['update_flag'].value_counts().to_dict()}")
    print(f"        report_type 取值分布: {one['report_type'].value_counts().to_dict()}")

print("\n=== E) 单公告日的报告期分布（判断一次增量覆盖多少报告期）===")
if income_day is not None and len(income_day):
    print(f"        end_date 取值: {income_day['end_date'].value_counts().to_dict()}")
    print(f"        report_type 取值: {income_day['report_type'].value_counts().to_dict()}")
    print(f"        update_flag 取值: {income_day['update_flag'].value_counts().to_dict()}")
    print(f"        涉及股票数: {income_day['ts_code'].nunique()}")

print("\n探针结束。")
