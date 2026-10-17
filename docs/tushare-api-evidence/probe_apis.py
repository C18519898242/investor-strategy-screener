"""第 1 集研究用探针：只读调用 Tushare，确认接口可用性、字段名与单位口径。

刻意不打印 token；只打印字段列表、行数与样例行。
"""

import sys
import time
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "screener").is_dir())
sys.path.insert(0, str(ROOT))

from screener.config import load_settings
from screener.sync import create_tushare_client

pro = create_tushare_client(load_settings())


def probe(label, api, **params):
    try:
        frame = getattr(pro, api)(**params)
    except Exception as error:  # noqa: BLE001
        print(f"[FAIL ] {label} | {api} {params} -> {type(error).__name__}: {str(error)[:180]}")
        return None
    time.sleep(0.4)
    if frame is None or len(frame) == 0:
        print(f"[EMPTY] {label} | {api} {params} -> 0 行")
        return frame
    print(f"[OK   ] {label} | {api} {params} -> {len(frame)} 行")
    print("        字段: " + ", ".join(str(c) for c in frame.columns))
    print("        样例: " + frame.head(1).to_json(orient="records", force_ascii=False))
    return frame


print("=== 1) 交易日历，取最近一个交易日 ===")
cal = probe("交易日历", "trade_cal", exchange="SSE", start_date="20260915", end_date="20261017")
open_days = []
if cal is not None:
    open_days = sorted(cal.loc[cal["is_open"] == 1, "cal_date"].astype(str).tolist())
    print(f"        最近开市日: {open_days[-3:]}")
trade_date = open_days[-1] if open_days else "20261016"

print("\n=== 2) 股票列表与股票池字段 ===")
basic = probe("上市股票", "stock_basic", exchange="", list_status="L")
if basic is not None:
    print("        market 取值: " + str(basic["market"].value_counts().to_dict()))
    print("        exchange 取值: " + str(basic["exchange"].value_counts().to_dict()))
probe("退市股票", "stock_basic", exchange="", list_status="D")
probe("暂停上市", "stock_basic", exchange="", list_status="P")

print(f"\n=== 3) 行情与每日指标（交易日 {trade_date}）===")
probe("日线行情", "daily", trade_date=trade_date)
db = probe("每日指标", "daily_basic", trade_date=trade_date)
if db is not None:
    row = db.iloc[0]
    print(
        "        total_mv 单位核对: total_mv=%s, close*total_share=%s (万元)"
        % (row.get("total_mv"), round(float(row["close"]) * float(row["total_share"]), 2))
    )
probe("停复牌", "suspend_d", trade_date=trade_date)

print("\n=== 4) 曾用名（历史 ST 判断）===")
probe("曾用名", "namechange", ts_code="000004.SZ")

print("\n=== 5) 申万行业分类（排除银行、保险）===")
classify = probe("行业分类", "index_classify", level="L1", src="SW2021")
if classify is not None:
    print("        L1 行业: " + ", ".join(classify["name"].astype(str).tolist()))
probe("行业成分(按股票)", "index_member_all", ts_code="600519.SH")

print("\n=== 6) 财务三表：累计与单季 ===")
probe("利润表(累计)", "income", ts_code="600519.SH", period="20241231")
probe("利润表(单季)", "income", ts_code="600519.SH", period="20240930", report_type="2")
probe("资产负债表", "balancesheet", ts_code="600519.SH", period="20241231")
probe("财务指标", "fina_indicator", ts_code="600519.SH", period="20241231")

print("\n=== 7) 分红 ===")
probe("分红(全部)", "dividend", ts_code="600519.SH")
probe("分红(按年度)", "dividend", ts_code="600519.SH", end_date="20241231")
