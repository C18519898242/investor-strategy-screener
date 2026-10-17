"""第 1 集研究用探针（第七轮）：行业排除能否按行业批量取（决定 2 次调用还是 5577 次）。只读。"""

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

FIELDS = "ts_code,name,l1_code,l1_name,l2_name,l3_name,in_date,out_date,is_new"


def call(label, api, tries=3, pause=6, **params):
    for attempt in range(1, tries + 1):
        try:
            frame = getattr(pro, api)(**params)
            time.sleep(pause)
            rows = 0 if frame is None else len(frame)
            print(f"[OK   ] {label:<44} -> {rows} 行")
            return frame
        except Exception as error:  # noqa: BLE001
            if attempt == tries:
                print(f"[FAIL ] {label:<44} -> {type(error).__name__}: {str(error)[:80]}")
                return None
            time.sleep(15)
    return None


print("=== 按行业批量取成分（银行 801780.SI）===")
frame = call("l1_code + fields", "index_member_all", l1_code="801780.SI", fields=FIELDS)
if frame is not None and len(frame):
    print("        样例: " + frame.head(3).to_json(orient="records", force_ascii=False))

frame2 = call("l1_code + is_new=Y + fields", "index_member_all", l1_code="801780.SI", is_new="Y", fields=FIELDS)
if frame2 is not None and len(frame2):
    print("        样例: " + frame2.head(3).to_json(orient="records", force_ascii=False))

frame3 = call("l1_code + is_new=N + fields", "index_member_all", l1_code="801780.SI", is_new="N", fields=FIELDS)
if frame3 is not None and len(frame3):
    print("        样例: " + frame3.head(3).to_json(orient="records", force_ascii=False))

print("\n=== 无参数全量（看是否支持整表拉取）===")
frame4 = call("仅 fields", "index_member_all", fields=FIELDS)
if frame4 is not None and len(frame4):
    print("        l1_name 分布(前 8): " + str(frame4["l1_name"].value_counts().head(8).to_dict()))
    print("        is_new 分布: " + str(frame4["is_new"].value_counts().to_dict()))

print("\n=== 保险（L2 保险Ⅱ 801784.SI?）逐股对照 ===")
for code in ("601601.SH", "601336.SH", "000627.SZ"):
    row = call(f"ts_code={code}", "index_member_all", ts_code=code, fields=FIELDS)
    if row is not None and len(row):
        item = row.iloc[0]
        print(f"        {code}: {item['name']} L1={item['l1_name']} L2={item['l2_name']}")
