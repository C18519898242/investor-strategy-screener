"""第 1 集研究用探针（第二轮）：解决第一轮的失败项与关键口径疑点。只读。"""

import sys
import time
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "screener").is_dir())
sys.path.insert(0, str(ROOT))

import tushare as ts

from screener.config import load_settings

PROXY = load_settings().tushare_api_url
TOKEN = load_settings().tushare_token


def client():
    pro = ts.pro_api(TOKEN, timeout=120)
    pro._DataApi__http_url = PROXY
    return pro


pro = client()


def call(label, api, tries=2, **params):
    for attempt in range(1, tries + 1):
        started = time.time()
        try:
            frame = getattr(pro, api)(**params)
        except Exception as error:  # noqa: BLE001
            print(f"[FAIL ] {label} | {api} {params} -> {type(error).__name__}: {str(error)[:120]}")
            if attempt == tries:
                return None
            continue
        elapsed = time.time() - started
        if frame is None or len(frame) == 0:
            print(f"[EMPTY] {label} | {api} {params} -> 0 行 ({elapsed:.1f}s)")
            return frame
        print(f"[OK   ] {label} | {api} {params} -> {len(frame)} 行 ({elapsed:.1f}s)")
        return frame
    return None


TRADE_DATE = "20261016"

print("=== A) stock_basic：不带空 exchange 参数重试 ===")
basic = call("上市股票(不传 exchange)", "stock_basic", list_status="L")
if basic is not None:
    print("        market 取值: " + str(basic["market"].value_counts().to_dict()))
    print("        exchange 取值: " + str(basic["exchange"].value_counts().to_dict()))
    print("        含 ST 的当前名称数: " + str(basic["name"].str.contains("ST", na=False).sum()))
    print("        名称含'退'的数量: " + str(basic["name"].str.contains("退", na=False).sum()))
    print("        industry 取值(前 12): " + str(basic["industry"].value_counts().head(12).to_dict()))
for status in ("D", "P"):
    probe = call(f"list_status={status}", "stock_basic", list_status=status)
    if probe is not None:
        print(f"        {status} 行数={len(probe)}, 样例={probe['name'].head(2).tolist()}")

print("\n=== B) daily 全市场单日（加大超时后重试）===")
daily = call("日线行情", "daily", trade_date=TRADE_DATE)
if daily is not None:
    print("        字段: " + ", ".join(str(c) for c in daily.columns))

print(f"\n=== C) 停牌股在 daily_basic 里是否仍有记录（{TRADE_DATE}）===")
sus = call("停牌名单", "suspend_d", trade_date=TRADE_DATE, suspend_type="S")
db = call("每日指标", "daily_basic", trade_date=TRADE_DATE)
if sus is not None and db is not None:
    suspended = set(sus["ts_code"].astype(str))
    present = suspended & set(db["ts_code"].astype(str))
    print(f"        停牌 {len(suspended)} 只，其中 daily_basic 仍有记录 {len(present)} 只")
    print(f"        样例: {sorted(present)[:5]}")
    daily_rows = call("日线行情", "daily", trade_date=TRADE_DATE)
    if daily_rows is not None:
        in_daily = suspended & set(daily_rows["ts_code"].astype(str))
        print(f"        其中 daily 仍有行情 {len(in_daily)} 只，样例: {sorted(in_daily)[:5]}")

print("\n=== D) 申万行业分类：参数组合重试 ===")
call("行业分类(默认)", "index_classify")
call("行业分类(src=SW2021)", "index_classify", src="SW2021")
call("行业分类(level=L1)", "index_classify", level="L1")
call("行业分类(level=L1,src=SW2021,只取字段)", "index_classify", level="L1", src="SW2021", fields="index_code,industry_name,level,src")
hist = call("行业成分(含历史 is_new=N)", "index_member_all", ts_code="600519.SH", is_new="N")
if hist is not None:
    print("        样例: " + hist.head(3).to_json(orient="records", force_ascii=False))

print("\n=== E) ST 列表接口是否可用 ===")
call("ST 列表", "stock_st", trade_date=TRADE_DATE)

print("\n=== F) 单季利润表 4 个季度求和 vs 年报累计（600519.SH 2024）===")
quarters = ["20240331", "20240630", "20240930", "20241231"]
total = 0.0
for period in quarters:
    single = call(f"单季 {period}", "income", ts_code="600519.SH", period=period, report_type="2", tries=1)
    if single is not None and len(single):
        value = float(single.iloc[0]["n_income_attr_p"])
        total += value
        print(f"        {period} 单季归母净利润 = {value:,.0f} 元")
annual = call("年报累计 20241231", "income", ts_code="600519.SH", period="20241231", tries=1)
if annual is not None and len(annual):
    reported = float(annual.iloc[0]["n_income_attr_p"])
    print(f"        4 个单季之和 = {total:,.0f} 元；年报累计 = {reported:,.0f} 元；差 = {total - reported:,.0f} 元")

print("\n=== G) 分红：显式取 base_share 并核对金额单位 ===")
div = call(
    "分红(指定字段)",
    "dividend",
    ts_code="600519.SH",
    fields="ts_code,end_date,ann_date,div_proc,cash_div,cash_div_tax,base_share,record_date,ex_date,pay_date",
)
if div is not None:
    print("        div_proc 取值分布: " + str(div["div_proc"].value_counts().to_dict()))
    implemented = div[div["div_proc"] == "实施"]
    print(f"        div_proc=实施 的行数: {len(implemented)}")
    for row in implemented.head(3).to_dict("records"):
        cash = row.get("cash_div_tax")
        shares = row.get("base_share")
        amount = (float(cash) * float(shares)) if cash is not None and shares is not None else None
        print(
            f"        end_date={row['end_date']} 每股税前={cash} 基准股本={shares}(万股) "
            f"→ 合计={amount:,.2f}(万元)" if amount is not None else "        字段缺失"
        )
