"""第 1 集研究用探针（第四轮）：验证「显式 fields 是否为必需」这一代理行为规律。只读。"""

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

BASIC_FIELDS = "ts_code,name,industry,market,exchange,list_status,list_date,delist_date"


def call(label, api, **params):
    try:
        frame = getattr(pro, api)(**params)
    except Exception as error:  # noqa: BLE001
        print(f"[FAIL ] {label:<38} -> {type(error).__name__}: {str(error)[:80]}")
        time.sleep(2.5)
        return None
    rows = 0 if frame is None else len(frame)
    print(f"[OK   ] {label:<38} -> {rows} 行")
    time.sleep(2.5)
    return frame


print("=== A) stock_basic 矩阵：fields 是否为必需 ===")
call("list_status=L（不传 fields）", "stock_basic", list_status="L")
call("list_status=L + fields", "stock_basic", list_status="L", fields=BASIC_FIELDS)
call("exchange=SSE + fields", "stock_basic", exchange="SSE", fields=BASIC_FIELDS)
call("list_status=P + fields", "stock_basic", list_status="P", fields=BASIC_FIELDS)
call("list_status=L,G（多值）+ fields", "stock_basic", list_status="L,G", fields=BASIC_FIELDS)

print("\n=== B) index_member_all 矩阵 ===")
call("ts_code + fields", "index_member_all", ts_code="600519.SH", fields="ts_code,l1_name,in_date,out_date,is_new")
call("l1_code + fields", "index_member_all", l1_code="801780.SI", fields="ts_code,name,l1_name,in_date,out_date,is_new")
call("l1_code + is_new=N + fields", "index_member_all", l1_code="801780.SI", is_new="N", fields="ts_code,name,l1_name,in_date,out_date,is_new")
call("is_new=N + fields（不限行业）", "index_member_all", is_new="N", fields="ts_code,name,l1_name,in_date,out_date,is_new")

print("\n=== C) dividend 矩阵（含 base_share）===")
div = call("ts_code（不传 fields）", "dividend", ts_code="600519.SH")
div2 = call(
    "ts_code + fields(含 base_share)",
    "dividend",
    ts_code="600519.SH",
    fields="ts_code,end_date,ann_date,div_proc,cash_div_tax,base_share,ex_date,pay_date",
)
for frame in (div, div2):
    if frame is None:
        continue
    print("        字段: " + ", ".join(str(c) for c in frame.columns))
    if "div_proc" in frame.columns:
        print("        div_proc 分布: " + str(frame["div_proc"].value_counts().to_dict()))
    if "base_share" in frame.columns:
        done = frame[frame["div_proc"] == "实施"] if "div_proc" in frame.columns else frame
        for row in done.head(3).to_dict("records"):
            cash, shares = row.get("cash_div_tax"), row.get("base_share")
            text = f"          end_date={row.get('end_date')} 每股税前={cash} 基准股本={shares}(万股)"
            if cash is not None and shares is not None:
                text += f" → 合计={float(cash) * float(shares):,.2f}(万元)"
            print(text)
    break

print("\n=== D) 财报接口：多年一次取回 ===")
call("income 按公告日区间", "income", ts_code="600519.SH", start_date="20240101", end_date="20261231",
     fields="ts_code,end_date,ann_date,f_ann_date,report_type,update_flag,n_income_attr_p")
call("balancesheet 按报告期区间", "balancesheet", ts_code="600519.SH", start_date="20240101", end_date="20261231",
     fields="ts_code,end_date,ann_date,report_type,update_flag,total_cur_assets,total_cur_liab,total_ncl,total_hldr_eqy_exc_min_int")
call("fina_indicator 按报告期区间", "fina_indicator", ts_code="600519.SH", start_date="20230101", end_date="20261231",
     fields="ts_code,end_date,ann_date,update_flag,roe_waa")
