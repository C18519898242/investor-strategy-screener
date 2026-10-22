"""分红送配：只存已经实施的方案，一个方案一行。

为什么不存预案
    上游对同一笔分红会按进度追加行（预案 → 股东大会通过 → 实施，实测还有预披露、
    股东提议、股东大会未通过、未通过、停止实施），抽样 40 只里进度行有 1,932 行、
    真正的实施行只占 23%。第 1 集只按已实施的现金分红算股息率，未实施的方案与
    「没有记录」等价，存下来只会让下游每次先判断用哪一行。

一个方案一行的边界
    实测同一份 (ts_code, end_date) 下存在多条实施行（600519 2024 年报有两条金额相同、
    公告日不同的实施行），按 `VERSION_COLUMNS` 取最新一条压成一行。同一个 `end_date`
    下没有出现过两条金额不同的实施行（100 个方案里 0 个），所以主键用两列够。

跨报告期的重复
    宁德时代 2024 中期那笔 1.23 元被上游同时挂在 `20240930` 和 `20241211` 下，
    `ex_date` 都是 `20250124`——两列主键压不掉。这类按 (ts_code, ex_date, cash_div_tax)
    再收一次，保留报告期较早的那条（`collapse_cross_period`）。

增量按股权登记日走
    `start_date` 这个接口不支持（实测单独传报 `50101`，带 `ts_code` 时被无视），
    `end_date` 只在「按报告期取全市场」时有用。真正能当水位的是 `record_date`：
    它是精确匹配的单日查询（实测 `record_date=20250626` 返回 137 行），所以增量是
    「`MAX(record_date)` 之后的每个交易日各取一次」，靠 `trade_cal` 枚举。

为什么走直连端点而不是 SDK 客户端
    生产 SDK 代理（`TUSHARE_API_URL`）不接受 `end_date` 单独过滤：实测
    `dividend(end_date='20260930')` 返回 `400 invalid_params`。这个数据集需要
    「不带 ts_code 的按日/按期查询」，只能走把参数直通上游的直连代理。
"""

import json
import logging
import sqlite3
import urllib.error
import urllib.request
from datetime import date

from screener.config import load_settings
from screener.sync.errors import SyncError
from screener.sync.retry import call_with_retry
from screener.sync.versions import keep_latest as _keep_latest

logger = logging.getLogger(__name__)

DATASET = "分红送配"
IMPLEMENTED = "实施"
PAGE_SIZE = 2000
TIMEOUT_SECONDS = 300
ENDPOINT = "https://datahubco.com/api/tushare"

COLUMNS: tuple[str, ...] = (
    "ts_code",
    "end_date",
    "ann_date",
    "div_proc",
    "stk_div",
    "stk_bo_rate",
    "stk_co_rate",
    "cash_div",
    "cash_div_tax",
    "record_date",
    "ex_date",
    "pay_date",
    "div_listdate",
    "imp_ann_date",
    "base_date",
    "base_share",
)
KEY = ("ts_code", "end_date")
VERSION_COLUMNS = ("imp_ann_date", "ann_date")

_UPSERT = (
    f"INSERT INTO dividend ({', '.join(COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in COLUMNS)}) "
    f"ON CONFLICT ({', '.join(KEY)}) DO UPDATE SET "
    + ", ".join(f"{name} = excluded.{name}" for name in COLUMNS if name not in KEY)
)


def keep_latest(rows: list[tuple]) -> list[tuple]:
    """同一`KEY`只留一行，取 `VERSION_COLUMNS` 最大的那条。"""
    return _keep_latest(rows, columns=COLUMNS, key=KEY, version=VERSION_COLUMNS)


def _fetch_page(record_date: str, offset: int) -> tuple[list[dict], bool]:
    """取一个股权登记日的一页，返回（行, 还有没有下一页）。"""
    token = load_settings().tushare_token.strip()
    body = json.dumps(
        {
            "api_name": "dividend",
            "token": token,
            "params": {"record_date": record_date, "offset": offset},
            "fields": ",".join(COLUMNS),
        }
    ).encode()
    request = urllib.request.Request(
        f"{ENDPOINT}/dividend", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read().decode() or "{}")
    if payload.get("code") != 0:
        code = payload.get("code")
        message = str(payload.get("msg") or "")
        if code == 429 or "concurrent" in message:
            raise SyncError(f"rate_limited：{message}")
        raise SyncError(f"code={code} {message}")
    data = payload["data"]
    fields = data["fields"]
    rows = [dict(zip(fields, item, strict=False)) for item in data["items"]]
    return rows, bool(data.get("has_more"))


def _store(connection: sqlite3.Connection, rows: list[dict]) -> int:
    """把一次返回写进表，只留实施行、一个方案一行。"""
    picked = [
        tuple(row.get(name) for name in COLUMNS)
        for row in rows
        if row.get("div_proc") == IMPLEMENTED
    ]
    if not picked:
        return 0
    merged = keep_latest(picked)
    connection.executemany(_UPSERT, merged)
    return len(merged)


def collapse_cross_period(connection: sqlite3.Connection) -> int:
    """同一笔分红被挂在两个报告期时只留报告期较早的那条，返回删掉的行数。"""
    cursor = connection.execute(
        """
        DELETE FROM dividend AS later
        WHERE later.ex_date IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM dividend AS earlier
              WHERE earlier.ts_code = later.ts_code
                AND earlier.ex_date = later.ex_date
                AND earlier.cash_div_tax IS later.cash_div_tax
                AND earlier.end_date < later.end_date
          )
        """
    )
    return cursor.rowcount


def water_level(connection: sqlite3.Connection, day: date) -> str | None:
    """本地覆盖到哪一天：不超过 `day` 的最大股权登记日。

    回填结果里可能已经有未来的登记日（已公告、还没到登记日的方案）。水位一旦越过
    今天，`MAX(record_date)` 之后就没有交易日了，菜单会把今天到这个未来日期之间
    新增的方案全部漏掉。所以水位按今天截断。
    """
    return connection.execute(
        "SELECT MAX(record_date) FROM dividend WHERE record_date <= ?",
        (day.strftime("%Y%m%d"),),
    ).fetchone()[0]


def record_dates_after_water(connection: sqlite3.Connection, day: date) -> tuple[str, ...]:
    """水位之后的每个交易日，按时间升序。

    表是空的时候返回空：分红的历史由 `scripts/dividend_backfill.py` 灌，
    菜单只负责增量，不去猜一个起点。
    """
    water = water_level(connection, day)
    if water is None:
        logger.info("%s 本地还没有数据，先跑 scripts/dividend_backfill.py", DATASET)
        return ()
    return tuple(
        row[0]
        for row in connection.execute(
            """
            SELECT cal_date FROM trade_cal
            WHERE exchange = 'SSE' AND is_open = 1 AND cal_date > ? AND cal_date <= ?
            ORDER BY cal_date
            """,
            (water, day.strftime("%Y%m%d")),
        )
    )


def _fetch_record_date(connection: sqlite3.Connection, record_date: str) -> int:
    """取一个股权登记日的全部实施方案。"""
    rows: list[dict] = []
    offset = 0
    while True:
        page, has_more = call_with_retry(lambda offset=offset: _fetch_page(record_date, offset))
        rows.extend(page)
        if not has_more or not page:
            break
        offset += len(page)
    written = _store(connection, rows)
    logger.info("%s 股权登记日 %s：写入 %d 行", DATASET, record_date, written)
    return written


def fetch_dividend(
    connection: sqlite3.Connection, client: object, *, today: date | None = None
) -> int:
    """把 `MAX(record_date)` 之后的每个交易日补齐。

    `client` 是登记表统一传进来的 SDK 客户端，这个数据集用不上——见模块说明。
    """
    day = today or date.today()
    dates = record_dates_after_water(connection, day)
    written = 0
    for record_date in dates:
        written += _fetch_record_date(connection, record_date)
    if dates:
        collapse_cross_period(connection)
    return written
