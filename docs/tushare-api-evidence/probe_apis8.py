"""第 1 集研究用探针（第八轮）：行业排除的最省调用路径。只读。"""

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


def call(label, api, tries=2, pause=6, **params):
    for attempt in range(1, tries + 1):
        try:
            frame = getattr(pro, api)(**params)
            time.sleep(pause)
            rows = 0 if frame is None else len(frame)
            print(f"[OK   ] {label:<40} -> {rows} 行")
            return frame
        except Exception as error:  # noqa: BLE001
            if attempt == tries:
                print(f"[FAIL ] {label:<40} -> {type(error).__name__}: {str(error)[:80]}")
                return None
            time.sleep(15)
    return None


print("=== A) stock_basic.industry 取值（能否直接识别银行/保险）===")
basic = call("上市股票 + industry", "stock_basic", list_status="L", fields="ts_code,name,industry,market")
if basic is not None:
    counts = basic["industry"].value_counts()
    print(f"        industry 取值个数: {len(counts)}")
    print("        前 15: " + str(counts.head(15).to_dict()))
    for key in ("银行", "保险", "证券", "多元金融", "房地产"):
        hit = basic[basic["industry"] == key]
        print(f"        industry == '{key}': {len(hit)} 只 {hit['name'].head(4).tolist()}")
    st_like = basic[basic["name"].str.contains("ST", na=False)]
    print(f"        名称含 ST: {len(st_like)} 只（对照 stock_st 的 201 只）")

print("\n=== B) index_member_all 是否支持 limit/offset 分页 ===")
first = call("limit=2000 offset=0", "index_member_all", fields="ts_code,l1_name,l2_name,is_new", limit=2000, offset=0)
second = call("limit=2000 offset=2000", "index_member_all", fields="ts_code,l1_name,l2_name,is_new", limit=2000, offset=2000)
if first is not None and second is not None and len(first) and len(second):
    print(f"        第一页首行={first.iloc[0]['ts_code']}，第二页首行={second.iloc[0]['ts_code']}")
    print(f"        两页是否相同: {first.iloc[0]['ts_code'] == second.iloc[0]['ts_code']}")
    print(f"        第一页 l1_name 前 6: {first['l1_name'].value_counts().head(6).to_dict()}")
