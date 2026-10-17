"""第 1 集研究用探针（第三轮）：钉死剩余疑点。只读，带间隔与重试。"""

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

TRADE_DATE = "20261016"


def call(label, api, tries=3, pause=1.5, **params):
    for attempt in range(1, tries + 1):
        try:
            frame = getattr(pro, api)(**params)
            time.sleep(pause)
            if frame is None or len(frame) == 0:
                print(f"[EMPTY] {label} | {api} {params} -> 0 行")
                return frame
            print(f"[OK   ] {label} | {api} {params} -> {len(frame)} 行")
            return frame
        except Exception as error:  # noqa: BLE001
            short = str(error)[:110]
            if attempt == tries:
                print(f"[FAIL ] {label} | {api} {params} -> {type(error).__name__}: {short}")
                return None
            time.sleep(3)
    return None


print("=== A) stock_basic 参数组合 ===")
for params in (
    {"exchange": "SSE", "list_status": "L"},
    {"exchange": "SZSE", "list_status": "L"},
    {"ts_code": "600519.SH"},
):
    frame = call(f"stock_basic {params}", "stock_basic", **params)
    if frame is not None:
        print("        字段: " + ", ".join(str(c) for c in frame.columns))
        if "market" in frame.columns and len(frame) > 100:
            print("        market 取值: " + str(frame["market"].value_counts().to_dict()))
            print("        industry 前 10: " + str(frame["industry"].value_counts().head(10).to_dict()))

print("\n=== B) 停牌股是否仍在 daily_basic / daily 中 ===")
sus = call("停牌名单", "suspend_d", trade_date=TRADE_DATE, suspend_type="S")
db = call("每日指标", "daily_basic", trade_date=TRADE_DATE, fields="ts_code,trade_date,close,total_mv")
dy = call("日线行情", "daily", trade_date=TRADE_DATE, fields="ts_code,trade_date,close")
if sus is not None:
    suspended = set(sus["ts_code"].astype(str))
    print(f"        停牌 {len(suspended)} 只: {sorted(suspended)}")
    for name, frame in (("daily_basic", db), ("daily", dy)):
        if frame is not None:
            hit = suspended & set(frame["ts_code"].astype(str))
            print(f"        {name} 中仍有记录: {len(hit)} 只 {sorted(hit)}")

print("\n=== C) 分红：显式取 base_share ===")
div = call(
    "分红",
    "dividend",
    ts_code="600519.SH",
    fields="ts_code,end_date,ann_date,div_proc,cash_div_tax,base_share,ex_date,pay_date",
)
if div is not None:
    print("        div_proc 分布: " + str(div["div_proc"].value_counts().to_dict()))
    done = div[div["div_proc"] == "实施"]
    print(f"        div_proc=实施 {len(done)} 行，样例:")
    for row in done.head(3).to_dict("records"):
        cash, shares = row.get("cash_div_tax"), row.get("base_share")
        line = f"          end_date={row['end_date']} 每股税前={cash} 基准股本={shares}(万股)"
        if cash is not None and shares is not None:
            line += f" → 合计={float(cash) * float(shares):,.2f}(万元)"
        print(line)

print("\n=== D) 申万行业历史成分（is_new=N，按行业取）===")
for l1 in ("801780.SI", "801120.SI"):
    frame = call(f"行业 {l1} 含历史", "index_member_all", l1_code=l1, is_new="N")
    if frame is not None:
        print("        字段: " + ", ".join(str(c) for c in frame.columns))
        print("        样例: " + frame.head(2).to_json(orient="records", force_ascii=False))

print("\n=== E) ST 列表字段与取值 ===")
st = call("ST 列表", "stock_st", trade_date=TRADE_DATE)
if st is not None:
    print("        字段: " + ", ".join(str(c) for c in st.columns))
    print("        type 分布: " + str(st["type"].value_counts().to_dict()))
    print("        样例: " + st.head(2).to_json(orient="records", force_ascii=False))

print("\n=== F) 退市股名单能否取到（用于股票池）===")
delisted = call("退市股票", "stock_basic", list_status="D", fields="ts_code,name,market,list_date,delist_date")
if delisted is not None:
    print("        market 取值: " + str(delisted["market"].value_counts().to_dict()))
    print("        样例: " + delisted.head(2).to_json(orient="records", force_ascii=False))
