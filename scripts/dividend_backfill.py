# ruff: noqa: E402
"""dividend（分红送配）全历史初始化脚本 —— 手动跑一次，不进 screener/。

正式代码（`screener/sync/dividend.py`）只负责增量更新：每次按最近一个已过披露截止日
的报告期取全市场。这个脚本负责把全市场历史一次性灌进库，只在重建数据库时用。

和另外三张表的区别
    * 只存已经实施的方案（`div_proc='实施'`）。上游对同一笔分红会按进度追加行
      （预案 → 股东大会通过 → 实施），抽样里实施行只占 23%，其余对第 1 集没有用。
    * 逐股一次调用就拿到全部历史，**不用翻页**：实测最长的股票也只有 98 行，
      远低于每页 2,000 行的上限。翻页逻辑仍然保留，只在上游真的返回 has_more 时才用。
    * 主键两列 (ts_code, end_date)。同一报告期的多条实施行按 (imp_ann_date, ann_date)
      取最新一条压成一行；被上游挂到两个报告期的同一笔，最后再按
      (ts_code, ex_date, cash_div_tax) 收一次，保留报告期较早的那条。

特点
    * 直连 datahubco：没有 1 年区间上限（SDK 路径有，会报 date_range_too_large）
    * 16 个字段全存，列名与 Tushare 完全同名
    * 断点续跑：每完成一只立刻记进 data/dividend-backfill-done.txt
    * 默认 20 并发。代理限频约 200 次/分钟，20 线程会频繁撞 429，脚本会等 30 秒重试；
      想跑得稳就调小，比如 --workers 8

用法（在仓库根目录）
    uv run --locked python scripts/dividend_backfill.py

    只看前 20 只股票试跑
    uv run --locked python scripts/dividend_backfill.py --limit 20

    减少并发（网络不稳时）
    uv run --locked python scripts/dividend_backfill.py --workers 8

中断随时可以按 Ctrl+C，下次启动会跳过已完成的请求。

    完全从头再来（清空表和续跑记录）
    uv run --locked python scripts/dividend_backfill.py --restart

只删表里的数据、不动续跑记录的话，那些股票会被当成"已完成"跳过，永远缺数据，
所以要重头来务必带 --restart。
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from screener.config import load_settings
from screener.store import open_database, publish_backfill
from screener.sync.dividend import (
    COLUMNS,
    DATASET,
    IMPLEMENTED,
    KEY,
    collapse_cross_period,
    keep_latest,
)

BASE = "https://datahubco.com/api/tushare"
DATABASE = ROOT / "data" / "screener.sqlite3"
DONE_FILE = ROOT / "data" / "dividend-backfill-done.txt"
PAGE_SIZE = 2000
MAX_ATTEMPTS = 8
RETRY_CODES = frozenset({429, 500, 502, 503, 504})
RATE_LIMIT_WAIT = 30
RETRY_WAIT = 5
PROGRESS_EVERY = 50

FIELDS = ",".join(COLUMNS)
VALUE_COLUMNS = [name for name in COLUMNS if name not in KEY]
UPSERT = (
    f"INSERT INTO dividend ({', '.join(COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in COLUMNS)}) "
    f"ON CONFLICT ({', '.join(KEY)}) DO UPDATE SET "
    + ", ".join(f"{name} = excluded.{name}" for name in VALUE_COLUMNS)
)


def read_done() -> dict[str, int | None]:
    """续跑记录，每行 "ts_code,当时写入的行数"。"""
    if not DONE_FILE.exists():
        return {}
    done: dict[str, int | None] = {}
    for line in DONE_FILE.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split(",")
        if not parts[0]:
            continue
        count = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None
        done[parts[0]] = count
    return done


def count_resume_mismatch(connection, done: dict[str, int | None]) -> int:
    """续跑记录说"当年有数据"、但库里一行都查不到的股票数。"""
    recorded = {code for code, count in done.items() if count is None or count > 0}
    if not recorded:
        return 0
    present = {row[0] for row in connection.execute("SELECT DISTINCT ts_code FROM dividend")}
    return len(recorded - present)


def fetch_page(token: str, ts_code: str, offset: int) -> tuple[list[tuple], bool]:
    """取一页，返回（行, 还有没有下一页）。只要已实施的方案。"""
    body = json.dumps(
        {
            "api_name": "dividend",
            "token": token,
            "params": {
                "ts_code": ts_code,
                "offset": offset,
            },
            "fields": FIELDS,
        }
    ).encode()
    request = urllib.request.Request(
        f"{BASE}/dividend", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        payload = json.loads(response.read().decode())
    if payload.get("code") != 0:
        raise RuntimeError(f"code={payload.get('code')} {payload.get('msg')}")
    data = payload["data"]
    fields = data["fields"]
    rows = [
        tuple(dict(zip(fields, item, strict=False)).get(name) for name in COLUMNS)
        for item in data["items"]
    ]
    return rows, bool(data.get("has_more"))


def fetch_with_retry(token: str, ts_code: str, offset: int) -> tuple[list[tuple], bool]:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return fetch_page(token, ts_code, offset)
        except urllib.error.HTTPError as error:
            if error.code not in RETRY_CODES or attempt == MAX_ATTEMPTS:
                raise
            wait = RATE_LIMIT_WAIT if error.code == 429 else RETRY_WAIT
            print(
                f"    ! {ts_code} offset={offset}：HTTP {error.code}，"
                f"{wait} 秒后重试（第 {attempt + 1}/{MAX_ATTEMPTS} 次）",
                flush=True,
            )
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError):
            if attempt == MAX_ATTEMPTS:
                raise
            print(
                f"    ! {ts_code} offset={offset}：连接异常，"
                f"{RETRY_WAIT} 秒后重试（第 {attempt + 1}/{MAX_ATTEMPTS} 次）",
                flush=True,
            )
            time.sleep(RETRY_WAIT)
        except RuntimeError as error:
            if attempt == MAX_ATTEMPTS:
                raise
            print(
                f"    ! {ts_code} offset={offset}：{error}，"
                f"{RATE_LIMIT_WAIT} 秒后重试（第 {attempt + 1}/{MAX_ATTEMPTS} 次）",
                flush=True,
            )
            time.sleep(RATE_LIMIT_WAIT)
    raise RuntimeError(f"{ts_code} offset={offset}：重试次数用尽")


def fetch_stock(token: str, ts_code: str) -> tuple[list[tuple], int]:
    """取完一只股票的全部历史，返回（去重后的实施行, 调用次数）。"""
    rows: list[tuple] = []
    calls = 0
    offset = 0
    while True:
        page, has_more = fetch_with_retry(token, ts_code, offset)
        calls += 1
        rows.extend(row for row in page if row[COLUMNS.index("div_proc")] == IMPLEMENTED)
        if not has_more:
            return keep_latest(rows), calls
        if not page:
            raise RuntimeError(f"{ts_code} offset={offset} 返回空页但 has_more=True")
        offset += len(page)


def format_duration(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def progress_line(finished: int, total: int, rows: int, failures: int, started: float) -> str:
    spent = time.perf_counter() - started
    rate = finished / spent if spent else 0.0
    percent = finished / total if total else 1.0
    remaining = (total - finished) / rate if rate else 0.0
    bar_width = 24
    filled = int(bar_width * percent)
    bar = "#" * filled + "." * (bar_width - filled)
    return (
        f"[{bar}] {percent:6.1%}  {finished}/{total} 只  "
        f"入库 {rows} 行  失败 {failures}  "
        f"{rate:.2f} 只/秒  已用 {format_duration(spent)}  "
        f"预计剩余 {format_duration(remaining)}"
    )


def print_summary(connection, rows: int, failures: int, removed: int, started: float) -> None:
    print("\n" + "=" * 78, flush=True)
    print(
        f"本次写入 {rows} 行，失败 {failures} 只，"
        f"耗时 {format_duration(time.perf_counter() - started)}",
        flush=True,
    )
    if removed:
        print(f"跨报告期重复收掉 {removed} 行", flush=True)
    total = connection.execute("SELECT COUNT(*) FROM dividend").fetchone()[0]
    stocks = connection.execute("SELECT COUNT(DISTINCT ts_code) FROM dividend").fetchone()[0]
    periods = connection.execute("SELECT COUNT(DISTINCT end_date) FROM dividend").fetchone()[0]
    print(f"表 dividend 现有 {total} 行，覆盖 {stocks} 只股票、{periods} 个报告期", flush=True)
    duplicated = connection.execute(
        "SELECT COUNT(*) FROM ("
        "  SELECT 1 FROM dividend GROUP BY ts_code, end_date HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    print(f"一个报告期多行的组：{duplicated}", flush=True)
    cross = connection.execute(
        "SELECT COUNT(*) FROM ("
        "  SELECT 1 FROM dividend WHERE ex_date IS NOT NULL"
        "  GROUP BY ts_code, ex_date, cash_div_tax HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    print(f"同一笔挂在多个报告期的组：{cross}", flush=True)
    missing = connection.execute(
        "SELECT COUNT(*) FROM stock_basic"
        " WHERE ts_code NOT IN (SELECT DISTINCT ts_code FROM dividend)"
    ).fetchone()[0]
    print(f"stock_basic 里还没有分红记录的股票：{missing} 只", flush=True)
    print("=" * 78, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="dividend 全历史初始化回填")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 只股票，用于试跑")
    parser.add_argument("--workers", type=int, default=20, help="并发线程数，默认 20")
    parser.add_argument(
        "--restart",
        action="store_true",
        help="清空 dividend 表并删除续跑记录，完全从头开始",
    )
    args = parser.parse_args()

    settings = load_settings()
    token = settings.tushare_token.strip()
    if not token:
        raise SystemExit("缺少 TUSHARE_TOKEN，检查仓库根目录的 .env")

    connection = open_database(DATABASE)
    if args.restart:
        removed = connection.execute("DELETE FROM dividend").rowcount
        if DONE_FILE.exists():
            DONE_FILE.unlink()
        print(f"--restart：已删除 dividend 表 {removed} 行和续跑记录，从头开始", flush=True)

    existing = connection.execute("SELECT COUNT(*) FROM dividend").fetchone()[0]

    codes = [
        row[0] for row in connection.execute("SELECT ts_code FROM stock_basic ORDER BY ts_code")
    ]
    if args.limit:
        codes = codes[: args.limit]
    if not codes:
        raise SystemExit("stock_basic 是空的，先同步基础资料")

    done = read_done()
    jobs = [code for code in codes if code not in done]
    mismatched = count_resume_mismatch(connection, done)

    print("=" * 78, flush=True)
    print("dividend 全历史初始化", flush=True)
    print(f"  数据库        {DATABASE}", flush=True)
    print(f"  已有数据      {existing} 行", flush=True)
    print(f"  股票          本次 {len(codes)} 只（stock_basic 全部）", flush=True)
    print(f"  口径          只存 div_proc={IMPLEMENTED}，一个方案一行", flush=True)
    print(f"  区间          全部历史，每页 {PAGE_SIZE} 行", flush=True)
    print(f"  并发          {args.workers} 个线程", flush=True)
    print(f"  续跑记录      {DONE_FILE}", flush=True)
    print(f"  上次已完成    {len(done)} 只，本次待处理 {len(jobs)} 只", flush=True)
    if mismatched:
        print("=" * 78, flush=True)
        print(
            f"  ！续跑记录里有 {mismatched} 只股票记为「已完成且当时有数据」，但库里一行都查不到。",
            flush=True,
        )
        print("    要重头来请加 --restart。", flush=True)
    print("=" * 78, flush=True)

    if not jobs:
        print("没有待处理的请求，全部已完成。", flush=True)
        removed = collapse_cross_period(connection)
        print_summary(connection, 0, 0, removed, time.perf_counter())
        connection.close()
        return

    started = time.perf_counter()
    rows_written = 0
    failures = 0
    print("开始取数……\n", flush=True)

    try:
        with DONE_FILE.open("a", encoding="utf-8") as done_handle:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(fetch_stock, token, code): code for code in jobs}
                for finished, future in enumerate(as_completed(futures), start=1):
                    ts_code = futures[future]
                    try:
                        rows, _ = future.result()
                    except Exception as error:  # noqa: BLE001
                        failures += 1
                        print(flush=True)
                        print(f"  x {ts_code} 失败：{error}", flush=True)
                        continue
                    if rows:
                        connection.executemany(UPSERT, rows)
                    rows_written += len(rows)
                    done_handle.write(f"{ts_code},{len(rows)}\n")
                    done_handle.flush()
                    line = progress_line(finished, len(jobs), rows_written, failures, started)
                    print(f"\r{line}", end="", flush=True)
                    if finished % PROGRESS_EVERY == 0 or finished == len(jobs):
                        print(flush=True)
    except KeyboardInterrupt:
        print("\n已中断。已完成的请求记录在续跑文件里，重新运行本脚本会接着往下跑。", flush=True)
        removed = collapse_cross_period(connection)
        print_summary(connection, rows_written, failures, removed, started)
        connection.close()
        raise SystemExit(130) from None

    removed = collapse_cross_period(connection)
    print_summary(connection, rows_written, failures, removed, started)
    if failures:
        print("有失败的股票。失败的不写续跑记录，重新运行本脚本会自动补这些。", flush=True)
    publish_backfill(
        connection,
        dataset=DATASET,
        row_count=connection.execute("SELECT COUNT(*) FROM dividend").fetchone()[0],
    )
    connection.close()


if __name__ == "__main__":
    main()
