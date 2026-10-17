"""第 1 集研究用探针（第六轮）：全市场批量接口（*_vip）与 B 股识别。只读。"""

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


def call(label, api, tries=2, pause=4, **params):
    for attempt in range(1, tries + 1):
        try:
            frame = getattr(pro, api)(**params)
            time.sleep(pause)
            rows = 0 if frame is None else len(frame)
            print(f"[OK   ] {label:<40} -> {rows} 行")
            return frame
        except Exception as error:  # noqa: BLE001
            if attempt == tries:
                print(f"[FAIL ] {label:<40} -> {type(error).__name__}: {str(error)[:90]}")
                return None
            time.sleep(15)
    return None


print("=== A) 全市场批量接口是否存在（决定同步成本）===")
call(
    "income_vip 全市场按报告期",
    "income_vip",
    period="20241231",
    fields="ts_code,end_date,ann_date,report_type,n_income_attr_p",
)
call(
    "balancesheet_vip 全市场按报告期",
    "balancesheet_vip",
    period="20241231",
    fields="ts_code,end_date,total_cur_assets,total_cur_liab,total_ncl,total_hldr_eqy_exc_min_int",
)
call("fina_indicator_vip 全市场按报告期", "fina_indicator_vip", period="20241231", fields="ts_code,end_date,roe_waa")
call("daily_basic 按报告期无 VIP 需求（对照）", "daily_basic", trade_date="20241231", fields="ts_code,total_mv")

print("\n=== B) 分红能否全市场按公告日取 ===")
div = call(
    "dividend 按公告日区间",
    "dividend",
    ann_date="20250403",
    fields="ts_code,end_date,ann_date,div_proc,cash_div_tax,base_share",
)
if div is not None:
    print("        字段: " + ", ".join(str(c) for c in div.columns))
    print("        样例: " + div.head(2).to_json(orient="records", force_ascii=False))

print("\n=== C) 股票列表是否含 B 股、北交所、CDR ===")
basic = call(
    "全市场上市股票",
    "stock_basic",
    list_status="L",
    fields="ts_code,symbol,name,market,exchange,list_status",
)
if basic is not None:
    print("        market 分布: " + str(basic["market"].value_counts().to_dict()))
    print("        exchange 分布: " + str(basic["exchange"].value_counts().to_dict()))
    symbols = basic["symbol"].astype(str)
    b_sh = basic[symbols.str.startswith("900")]
    b_sz = basic[symbols.str.startswith("200")]
    print(f"        疑似沪 B(900xxx): {len(b_sh)} 只 {b_sh['name'].head(3).tolist()}")
    print(f"        疑似深 B(200xxx): {len(b_sz)} 只 {b_sz['name'].head(3).tolist()}")
    print(f"        北交所: {len(basic[basic['market'] == '北交所'])} 只")
