# 原始层：每日指标（`daily_basic`）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpower-subagent-driven-development (recommended) or superpower-executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让菜单的「更新本地数据」真的把 `daily_basic`（每日指标）写进 SQLite：首次按交易日历回填约 11 年（逐日、约 2700 次调用），之后每次只补缺失的交易日，重复运行结果一致。

**Architecture:** 新增 `screener/sync/daily_basic.py`，一个接口一个模块；`store.py` 新增 `daily_basic` 表（18 列）；`sync/registry.py` 把中文数据名「每日指标」映射到抓取函数，更新菜单自动多出一项（菜单取自登记表）。写入复用既有的 `insert_rows`/`delete_rows`，不新增原语；筛选流程一行不改。

**Tech Stack:** Python 3.13、标准库 `sqlite3`/`unittest`/`datetime`、已安装的 `tushare` SDK、`pandas.DataFrame`。不新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-18-raw-layer-daily-basic-design.md`

## Global Constraints

- 本轮只接 `daily_basic` 一个接口；`income`、`balancesheet`、`fina_indicator`、`dividend` 不做。
- **一个交易日一次调用**，只传 `trade_date`；**不传** `ts_code`、**不传** `start_date`/`end_date`。
- 显式传 `fields`，18 个字段，顺序与 `COLUMNS` 一致。
- 数据名固定为 **`每日指标`**，只出现在 `registry.py` 与测试里，不出现 Tushare 接口名。
- 列名照抄接口字段、字段全部入库（D1）；日期一律 TEXT `YYYYMMDD`（D10）；原始层不存派生指标（D2）。
- 表结构固定：18 列（`ts_code`、`trade_date` 为 TEXT，其余 16 列为数值），主键 `(ts_code, trade_date)`，附加索引 `trade_date`。
- 待补集合 = 交易日历里 `is_open = 1`、`cal_date <= 止`、且库里没有任何该日行的交易日；**先删水位那一天**。
- 写入用 `DELETE FROM daily_basic WHERE trade_date = <水位>` + `executemany` 批量 INSERT；**不用** `INSERT OR REPLACE`，**不用** `ON CONFLICT`。
- **不按 `trade_date` 排序**写入（一天一次调用，天内顺序无意义；这一点与 `suspend_d` 不同）。
- 不额外引入事务管理，不做失败回滚，不加「返回 0 行」「行数恰好 6000」之类的护栏。
- 不做并发（业主裁定：担心并发写丢数据）。
- 抓取函数的可注入参数只有 `today: date | None`；重试走既有的 `call_with_retry`，不新增重试逻辑。
- 开发测试使用 `python -m unittest`，全部用假客户端，不联网。

## Review Focus

- **交易日历是前置数据**：`trade_cal` 为空时 0 次调用、0 行、**不报错不提示**；日历只到某天时止于该天。
- **水位那天可能是上次拉到一半的**：必须只删这一天，其余日期一行不动；删多了会丢数据。
- **中间空洞**：待补集合是真差集（日历的交易日减去库里已有行的日期），水位**以下**没有行的交易日也要补。
- **数值列与 `NaN`**：接口给 `null` 时要存成 SQL NULL，不能存成字符串 `'nan'`；16 个数值列都要走 `astype(object).where(notna(), None)`。
- **进度行必须在请求之前**：单次调用实测 17～38 秒，控制台靠它判断没卡死。
- **重复运行**：第二次运行后表的内容与行数必须与第一次完全一致，不重复、不报错。

---

### Task 1: `store.py` 加 `daily_basic` 表

**Files:**
- Modify: `screener/store.py`
- Test: `tests/test_store_raw_tables.py`

**Interfaces:**
- Consumes: 无（`store.py` 是既有模块；`insert_rows`/`delete_rows` 已存在，**本轮不加新原语**）。
- Produces: `open_database` 建出 `daily_basic` 表与 `daily_basic_trade_date` 索引。

- [ ] **Step 1: Write the failing test**

在 `tests/test_store_raw_tables.py` 顶部加常量和构造器：

```python
DAILY_BASIC_COLUMNS = (
    "ts_code",
    "trade_date",
    "close",
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
)


def daily_basic_row(trade_date, **overrides):
    row = dict.fromkeys(DAILY_BASIC_COLUMNS, 0.0)
    row["ts_code"] = "000001.SZ"
    row["trade_date"] = trade_date
    row["close"] = 10.0
    row["pe"] = 8.0
    return {**row, **overrides}
```

在 `RawTablesTest` 里加四条：

```python
    def test_daily_basic_table_has_the_decided_columns_and_primary_key(self):
        self.assertEqual(
            [name for name, _ in self._columns("daily_basic")],
            list(DAILY_BASIC_COLUMNS),
        )
        self.assertEqual([pk for _, pk in self._columns("daily_basic")], [1, 2] + [0] * 16)

    def test_daily_basic_rejects_the_same_stock_on_the_same_day(self):
        with self.assertRaises(sqlite3.IntegrityError):
            insert_rows(
                self.connection,
                table="daily_basic",
                columns=DAILY_BASIC_COLUMNS,
                rows=[daily_basic_row("20261016"), daily_basic_row("20261016")],
            )

    def test_daily_basic_stores_a_full_row_and_deletes_only_the_named_day(self):
        insert_rows(
            self.connection,
            table="daily_basic",
            columns=DAILY_BASIC_COLUMNS,
            rows=[daily_basic_row("20261015"), daily_basic_row("20261016", close=12.5, pe=None)],
        )

        removed = delete_rows(
            self.connection, table="daily_basic", column="trade_date", value="20261016"
        )

        self.assertEqual(removed, 1)
        self.assertEqual(
            self.connection.execute("SELECT trade_date, close, pe FROM daily_basic").fetchall(),
            [("20261015", 10.0, 8.0)],
        )

    def test_daily_basic_has_a_trade_date_index(self):
        names = [
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'daily_basic'"
            )
        ]

        self.assertIn("daily_basic_trade_date", names)
```

（顶部 import 需要补 `sqlite3`；`_columns` 是文件里已有的辅助方法。）

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.test_store_raw_tables -v`
Expected: FAIL —— `sqlite3.OperationalError: no such table: daily_basic`

- [ ] **Step 3: Implement in `screener/store.py`**

在 `open_database` 里 `suspend_d` 建表与建索引之后加：

```python
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_basic (
            ts_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            close REAL,
            turnover_rate REAL,
            turnover_rate_f REAL,
            volume_ratio REAL,
            pe REAL,
            pe_ttm REAL,
            pb REAL,
            ps REAL,
            ps_ttm REAL,
            dv_ratio REAL,
            dv_ttm REAL,
            total_share REAL,
            float_share REAL,
            free_share REAL,
            total_mv REAL,
            circ_mv REAL,
            PRIMARY KEY (ts_code, trade_date)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS daily_basic_trade_date ON daily_basic (trade_date)"
    )
```

- [ ] **Step 4: Run the whole suite to verify it passes**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -t .`
Expected: OK（94 tests）

- [ ] **Step 5: Commit**

```bash
git add screener/store.py tests/test_store_raw_tables.py
git commit -m "feat: add the daily_basic table"
```

---

### Task 2: `sync/daily_basic.py` 抓取模块

**Files:**
- Create: `screener/sync/daily_basic.py`
- Test: `tests/test_sync_daily_basic.py`

**Interfaces:**
- Consumes: `store.insert_rows`、`store.delete_rows`；`sync.retry.call_with_retry(operation, *, sleep=..., attempts=3)`（都已存在）。
- Produces:
  - `screener.sync.daily_basic.DATASET = "每日指标"`
  - `screener.sync.daily_basic.COLUMNS` —— 18 个字段名，顺序见 Global Constraints。
  - `fetch_daily_basic(connection: sqlite3.Connection, client: object, *, today: date | None = None) -> int` —— 返回本次写入的总行数。

- [ ] **Step 1: Write the failing test**

新建 `tests/test_sync_daily_basic.py`。假客户端只认 `trade_date`，并**断言没有别的参数**（Global Constraints 要求只传 `trade_date`）：

```python
import shutil
import sqlite3
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from screener.store import insert_rows, open_database
from screener.sync.daily_basic import COLUMNS, fetch_daily_basic

CALENDAR = [
    ("20261012", 1),
    ("20261013", 1),
    ("20261014", 1),
    ("20261015", 1),
    ("20261016", 1),
    ("20261017", 0),
    ("20261018", 0),
]
TODAY = date(2026, 10, 18)


class FakeClient:
    """按 trade_date 过滤的假客户端，只接受 trade_date 与 fields。"""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def daily_basic(self, **params):
        self.calls.append(params)
        fields = params["fields"].split(",")
        picked = [row for row in self.rows if row["trade_date"] == params["trade_date"]]
        return pd.DataFrame(
            [{name: row[name] for name in fields} for row in picked], columns=fields
        )


class InterruptingClient(FakeClient):
    """第 N 次调用直接抛 KeyboardInterrupt，模拟 Ctrl+C 打断。"""

    def __init__(self, rows=(), *, interrupt_on):
        super().__init__(rows)
        self.interrupt_on = interrupt_on

    def daily_basic(self, **params):
        if len(self.calls) + 1 == self.interrupt_on:
            raise KeyboardInterrupt
        return super().daily_basic(**params)


def row(trade_date, **overrides):
    values = dict.fromkeys(COLUMNS, 0.0)
    values["ts_code"] = "000001.SZ"
    values["trade_date"] = trade_date
    values["close"] = 10.0
    values["pe"] = 8.0
    return {**values, **overrides}


class DailyBasicTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        self.connection = open_database(self.directory / "screener.sqlite3")
        insert_rows(
            self.connection,
            table="trade_cal",
            columns=("exchange", "cal_date", "is_open", "pretrade_date"),
            rows=[("SSE", cal_date, is_open, "") for cal_date, is_open in CALENDAR],
        )

    def tearDown(self):
        self.connection.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def _trading_days(self):
        return [
            row[0]
            for row in self.connection.execute(
                "SELECT DISTINCT trade_date FROM daily_basic ORDER BY trade_date"
            )
        ]

    def test_backfills_every_trading_day_the_calendar_knows(self):
        client = FakeClient([row("20261012"), row("20261016")])

        count = fetch_daily_basic(self.connection, client, today=TODAY)

        self.assertEqual([call["trade_date"] for call in client.calls], [
            "20261012", "20261013", "20261014", "20261015", "20261016",
        ])
        self.assertEqual(set(client.calls[0]), {"trade_date", "fields"})
        self.assertEqual(client.calls[0]["fields"], ",".join(COLUMNS))
        self.assertEqual(count, 2)
        self.assertEqual(self._trading_days(), ["20261012", "20261016"])

    def test_water_level_day_is_deleted_and_refetched(self):
        fetch_daily_basic(
            self.connection, FakeClient([row("20261012"), row("20261013")]), today=TODAY
        )

        client = FakeClient([row("20261013")])
        fetch_daily_basic(self.connection, client, today=TODAY)

        self.assertEqual(
            [call["trade_date"] for call in client.calls],
            ["20261013", "20261014", "20261015", "20261016"],
        )
        self.assertEqual(self._trading_days(), ["20261012", "20261013"])

    def test_a_gap_below_the_water_level_is_filled(self):
        insert_rows(
            self.connection,
            table="daily_basic",
            columns=COLUMNS,
            rows=[row("20261012"), row("20261015")],
        )

        client = FakeClient([row("20261013"), row("20261014"), row("20261015")])
        fetch_daily_basic(self.connection, client, today=TODAY)

        self.assertEqual([call["trade_date"] for call in client.calls], [
            "20261013", "20261014", "20261015",
        ])

    def test_an_empty_calendar_fetches_nothing_and_does_not_raise(self):
        self.connection.execute("DELETE FROM trade_cal")
        client = FakeClient()

        count = fetch_daily_basic(self.connection, client, today=TODAY)

        self.assertEqual(count, 0)
        self.assertEqual(client.calls, [])

    def test_the_calendar_end_caps_the_backfill(self):
        self.connection.execute("DELETE FROM trade_cal WHERE cal_date > '20261013'")
        client = FakeClient()

        fetch_daily_basic(self.connection, client, today=TODAY)

        self.assertEqual([call["trade_date"] for call in client.calls], ["20261012", "20261013"])

    def test_every_trading_day_is_announced_before_its_request(self):
        timeline = []

        class Recorder(logging.Handler):
            def emit(self, record):
                timeline.append(("log", record.getMessage()))

        client = FakeClient()
        request = client.daily_basic

        def recording_request(**params):
            timeline.append(("fetch", params["trade_date"]))
            return request(**params)

        client.daily_basic = recording_request
        logger = logging.getLogger("screener.sync.daily_basic")
        recorder = Recorder()
        with self.assertLogs("screener.sync.daily_basic", level="INFO") as captured:
            logger.addHandler(recorder)
            try:
                fetch_daily_basic(self.connection, client, today=TODAY)
            finally:
                logger.removeHandler(recorder)

        self.assertEqual(len(captured.output), 5)
        self.assertEqual(timeline[0], ("log", "每日指标 第 1/5 个交易日：20261012"))
        self.assertEqual(timeline[1], ("fetch", "20261012"))
        self.assertEqual(timeline[-2], ("log", "每日指标 第 5/5 个交易日：20261016"))
        self.assertEqual(timeline[-1], ("fetch", "20261016"))
        self.assertEqual(len(timeline), 10)

    def test_null_values_are_stored_as_sql_null(self):
        fetch_daily_basic(self.connection, FakeClient([row("20261012", pe=None)]), today=TODAY)

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM daily_basic WHERE pe IS NULL"
            ).fetchone(),
            (1,),
        )

    def test_days_written_before_an_interruption_stay_on_disk(self):
        client = InterruptingClient([row("20261012"), row("20261013")], interrupt_on=3)

        with self.assertRaises(KeyboardInterrupt):
            fetch_daily_basic(self.connection, client, today=TODAY)

        reopened = sqlite3.connect(self.directory / "screener.sqlite3", timeout=0.2)
        try:
            self.assertEqual(
                [
                    record[0]
                    for record in reopened.execute(
                        "SELECT DISTINCT trade_date FROM daily_basic ORDER BY trade_date"
                    )
                ],
                ["20261012", "20261013"],
            )
        finally:
            reopened.close()
```

（顶部 import 需要 `logging`。）

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.test_sync_daily_basic -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'screener.sync.daily_basic'`

- [ ] **Step 3: Implement `screener/sync/daily_basic.py`**

```python
import logging
import sqlite3
import time
from datetime import date

from screener.store import delete_rows, insert_rows
from screener.sync.retry import call_with_retry

logger = logging.getLogger(__name__)

DATASET = "每日指标"
COLUMNS: tuple[str, ...] = (
    "ts_code",
    "trade_date",
    "close",
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
)
```

函数按 spec 第 4 节的六步写：

1. `end = (today or date.today()).strftime("%Y%m%d")`。
2. `water = connection.execute("SELECT MAX(trade_date) FROM daily_basic").fetchone()[0]`；不是 `None` 时先 DEBUG 记一行「水位 …，先删掉那天的旧行」，再 `delete_rows(connection, table="daily_basic", column="trade_date", value=water)`。
3. 查待补交易日（spec 第 4 节第 4 步的 SQL，参数是 `end`），取成一维列表：

```python
    days = [
        row[0]
        for row in connection.execute(
            """
            SELECT cal_date FROM trade_cal
            WHERE is_open = 1 AND cal_date <= ?
              AND cal_date NOT IN (SELECT trade_date FROM daily_basic)
            ORDER BY cal_date
            """,
            (end,),
        )
    ]
```

4. `total = len(days)`，然后 `for index, trade_date in enumerate(days, start=1):` —— 循环第一件事就是
   `logger.info("每日指标 第 %d/%d 个交易日：%s", index, total, trade_date)`，**必须在请求之前**。
5. 请求：先 DEBUG 记 `"daily_basic 请求：%s"`，再
   `frame = call_with_retry(lambda trade_date=trade_date: client.daily_basic(trade_date=trade_date, fields=",".join(COLUMNS)))`
   （照 `suspend_d.py` 的 lambda 默认参数写法，避免闭包读到下一轮的值），然后 DEBUG 记 `"daily_basic 返回 %d 行，耗时 %.1f 秒"`。
6. 写库并累加：

```python
        selected = frame[list(COLUMNS)]
        rows = selected.astype(object).where(selected.notna(), None)
        written += insert_rows(
            connection,
            table="daily_basic",
            columns=COLUMNS,
            rows=rows.itertuples(index=False, name=None),
        )
```

循环结束后 `return written`。**不做**排序（天内顺序无意义），**不加**行数校验。

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m unittest tests.test_sync_daily_basic -v`
Expected: OK（8 tests）

- [ ] **Step 5: Commit**

```bash
git add screener/sync/daily_basic.py tests/test_sync_daily_basic.py
git commit -m "feat: fetch daily_basic one trading day at a time"
```

---

### Task 3: 登记数据名并接进假客户端与菜单断言

**Files:**
- Modify: `screener/sync/registry.py`
- Modify: `tests/tushare_fakes.py`
- Test: `tests/test_sync_registry.py`、`tests/test_cli.py`

**Interfaces:**
- Consumes: `daily_basic.DATASET`、`daily_basic.fetch_daily_basic`、`daily_basic.COLUMNS`（Task 2）。
- Produces:
  - `registry.DATASET_FETCHERS["每日指标"] is fetch_daily_basic`
  - `registry.WATER_QUERIES["每日指标"] == "SELECT MAX(trade_date) FROM daily_basic"`
  - `FakeTushareClient(daily_basic_rows=...)` 与 `tushare_fakes.daily_basic_row(...)`

- [ ] **Step 1: Write the failing test**

`tests/tushare_fakes.py`：`__init__` 加 `daily_basic_rows=()` 并存起来；加方法与构造器（照 `suspend_d` 的样式）：

```python
def daily_basic_row(**overrides):
    """一行 daily_basic 的 18 列，默认全是 0.0，ts_code/trade_date 留空串。"""
    return {**dict.fromkeys(DAILY_BASIC_COLUMNS, 0.0), "ts_code": "", "trade_date": "", **overrides}
```

```python
    def daily_basic(self, **params):
        fields = self._record("daily_basic", params)
        picked = [row for row in self.daily_basic_rows if row["trade_date"] == params["trade_date"]]
        return self._slice(picked, fields)
```

（`DAILY_BASIC_COLUMNS` 从 `screener.sync.daily_basic` import `COLUMNS as DAILY_BASIC_COLUMNS`。）

`tests/test_sync_registry.py`：加 `DAILY_BASIC_ROWS`（用 `tushare_fakes.daily_basic_row`，所以顶部 import 那行补上它），`fake_client()` 传进去，并把三处数据集元组从三项扩成四项：

```python
DAILY_BASIC_ROWS = [daily_basic_row(ts_code="000001.SZ", trade_date="20261016", close=10.0)]
```

- `test_dispatches_every_registered_dataset`：元组加 `"每日指标"`，`counts` 期望加 `"每日指标": 1`，并断言 `SELECT COUNT(*) FROM daily_basic` 是 `(1,)`。
- `test_logs_progress_for_every_dataset`：元组加 `"每日指标"`，期望日志尾部补 `"INFO:screener.sync.registry:开始拉取 每日指标"` 与 `"INFO:screener.sync.registry:每日指标 完成：1 行"`。
- `test_water_levels_name_the_snapshot_and_the_incremental_datasets`：元组加 `"每日指标"`，期望里加 `"每日指标": "水位 20261016"`。

`tests/test_cli.py`：菜单来自登记表，所以会多出第四项。`test_the_main_menu_goes_straight_to_the_dataset_menu` 加一行断言：

```python
        self.assertIn("4. 每日指标（无数据，首次全量）", text)
```

`test_invalid_choice_is_asked_again` 的期望消息改成 `"编号应为 0~4 之间的数字"`（菜单现在是四项）。

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.test_sync_registry tests.test_cli -v`
Expected: FAIL —— `SyncError: 未登记的数据名：每日指标`，且 `TypeError: ... unexpected keyword argument 'daily_basic_rows'`

- [ ] **Step 3: Implement**

`screener/sync/registry.py`：顶部照 `suspend_d` 的样式加两行 import，两个表各加一项：

```python
    DAILY_BASIC_DATASET: fetch_daily_basic,
```

```python
    DAILY_BASIC_DATASET: "SELECT MAX(trade_date) FROM daily_basic",
```

`WATER_QUERIES` 顶部注释说「键必须与 `DATASET_FETCHERS` 一致」，`test_every_registered_dataset_has_a_water_query` 会检查，两个表都要加。

- [ ] **Step 4: Run the whole suite to verify it passes**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -t .`
Expected: OK（102 tests）

- [ ] **Step 5: Commit**

```bash
git add screener/sync/registry.py tests/tushare_fakes.py tests/test_sync_registry.py tests/test_cli.py
git commit -m "feat: register the 每日指标 dataset"
```

---

### Task 4: 同步既有文档

**Files:**
- Modify: `docs/data-model-proposal.md`（D8 的增量表）
- Modify: `docs/superpowers/specs/2026-10-11-tech-selection-architecture-design.md`（「同步」表）
- Modify: `product.md`（菜单示意与首次更新说明）
- Modify: `docs/tushare-api-evaluation.md`（`daily_basic` 小节）

**Interfaces:**
- Consumes: spec 第 8 节的五条。
- Produces: 无代码接口；改完这些文档对 `daily_basic` 的说法与本轮实现一致。

- [ ] **Step 1: 改 D8 的增量表**

`docs/data-model-proposal.md` 的 D8 增量表里 `daily_basic` 一行已有「用交易日历定出缺失的交易日，逐日补」，在耗时列或备注里补上「首次约 2700 次、13～29 小时」。

- [ ] **Step 2: 改架构文档的「同步」表**

`docs/superpowers/specs/2026-10-11-tech-selection-architecture-design.md` 的同步表里加同一行（措辞与本文件既有行一致），并补一句：`daily_basic` 的待补集合是「交易日历的交易日」减去「库里已有行的日期」，首次回填起点因此是日历的最早交易日。

- [ ] **Step 3: 改 `product.md`**

「更新本地数据」示例的数据集列表加上「每日指标」，并说明首次更新因为要按交易日逐日回填会明显变慢（约 2700 次调用、13～29 小时），可以随时中断、下次接着补。

- [ ] **Step 4: 改 `docs/tushare-api-evaluation.md` 的 `daily_basic` 小节**

补四条实测并修正两处数字：只给 `trade_date` 即返回当日全市场（5567 行、**18 列**，缺的 10 只全部是停牌股、重复 0 行）；不给 `ts_code` 时 `start_date`/`end_date` 报 `invalid_params`；多股票长区间报 `date_range_too_large`；单日调用 17～38 秒（中位约 23 秒），**把「每张表约 13 分钟」改成 13～29 小时**，列数从 15 改成 18。

- [ ] **Step 5: 检查与提交**

Run: `.venv\Scripts\ruff.exe check --no-cache .` 与 `.venv\Scripts\ruff.exe format --check --no-cache .`
Expected: 全部通过（本轮不动代码，只确认没有顺带改坏）

```bash
git add docs/data-model-proposal.md docs/superpowers/specs/2026-10-11-tech-selection-architecture-design.md product.md docs/tushare-api-evaluation.md
git commit -m "docs: record daily_basic in the incremental strategy tables"
```
