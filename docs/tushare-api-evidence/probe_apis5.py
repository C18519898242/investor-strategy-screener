"""第 1 集研究用探针（第五轮）：分红 base_share、行业排除口径、历史行情覆盖。只读，带间隔。"""

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


def call(label, api, tries=3, pause=5, **params):
    for attempt in range(1, tries + 1):
        try:
            frame = getattr(pro, api)(**params)
            time.sleep(pause)
            rows = 0 if frame is None else len(frame)
            print(f"[OK   ] {label:<34} -> {rows} 行")
            return frame
        except Exception as error:  # noqa: BLE001
            if attempt == tries:
                print(f"[FAIL ] {label:<34} -> {type(error).__name__}: {str(error)[:90]}")
                return None
            time.sleep(20)
    return None


print("=== A) 申万行业：银行 / 保险 / 证券 落在哪一级 ===")
for code, name in (("601398.SH", "工商银行"), ("601318.SH", "中国平安"), ("600030.SH", "中信证券"), ("000002.SZ", "万科A")):
    frame = call(f"{name} {code}", "index_member_all", ts_code=code, fields="ts_code,name,l1_name,l2_name,l3_name,in_date,out_date,is_new")
    if frame is not None and len(frame):
        row = frame.iloc[0]
        print(f"        {name}: L1={row['l1_name']} / L2={row['l2_name']} / L3={row['l3_name']}")

print("\n=== B) 历史行情覆盖（取价用）===")
for date in ("20241231", "20201231", "20151231"):
    frame = call(f"daily_basic {date}", "daily_basic", trade_date=date, fields="ts_code,close,total_mv")
    if frame is not None and len(frame):
        print(f"        {date} 字段: {list(frame.columns)}")

print("\n=== C) 交易日历 pretrade_date 是否可用 ===")
cal = call("trade_cal", "trade_cal", exchange="SSE", start_date="20261001", end_date="20261016", fields="exchange,cal_date,is_open,pretrade_date")
if cal is not None:
    print("        " + cal.tail(4).to_string(index=False).replace("\n", "\n        "))

print("\n=== D) dividend 取 base_share（重试）===")
div = call(
    "dividend + base_share",
    "dividend",
    tries=3,
    ts_code="600519.SH",
    fields="ts_code,end_date,ann_date,div_proc,cash_div_tax,base_share,base_date,ex_date,pay_date",
)
if div is not None:
    print("        字段: " + ", ".join(str(c) for c in div.columns))
    done = div[div["div_proc"] == "实施"]
    print(f"        实施 {len(done)} 行")
    for row in done.head(4).to_dict("records"):
        cash, shares = row.get("cash_div_tax"), row.get("base_share")
        text = f"          end_date={row['end_date']} 公告日={row['ann_date']} 每股税前={cash} 基准股本={shares}(万股)"
        if cash is not None and shares is not None:
            text += f" → 合计={float(cash) * float(shares):,.2f}(万元)"
        print(text)

print("\n=== E) income 按公告日分段的安全区间 ===")
for start, end in (("20240101", "20241231"), ("20240101", "20240630"), ("20240101", "20240331")):
    call(
        f"income 公告日 {start}~{end}",
        "income",
        tries=2,
        ts_code="600519.SH",
        start_date=start,
        end_date=end,
        fields="ts_code,end_date,ann_date,report_type,update_flag,n_income_attr_p",
    )
