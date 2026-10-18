# 原始层：停复牌（`suspend_d`）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpower-subagent-driven-development (recommended) or superpower-executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让菜单的「更新本地数据」真的把 `suspend_d`（停复牌）写进 SQLite：首次做 11 年回填，之后每次只从水位往前补，重复运行结果一致。

**Architecture:** 新增 `screener/sync/suspend_d.py`，一个接口一个模块；`store.py` 新增 `suspend_d` 表与两个写入原语（`insert_rows`、`delete_rows`），`replace_rows` 改为调用前者；`sync/registry.py` 把中文数据名「停复牌」映射到抓取函数；`mock_all_datasets` 策略登记这个新数据名，于是「更新范围」自动包含它。筛选流程一行不改。

**Tech Stack:** Python 3.13、标准库 `sqlite3`/`unittest`/`datetime`、已安装的 `tushare` SDK、`pandas.DataFrame`。不新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-18-raw-layer-suspend-d-design.md`

## Global Constraints

- 本轮只接 `suspend_d` 一个接口；`daily_basic` 与其余 4 个必需接口不做。
- 分片固定 **27 天**，不按自然月；回补窗口 **1 天**（只删水位那一天）。
- 数据名固定为 **`停复牌`**，只出现在 `registry.py` 与策略文件里，不出现 Tushare 接口名。
- 列名照抄接口字段、字段全部入库（D1）；日期一律 TEXT `YYYYMMDD`（D10）；原始层不存派生指标（D2）。
- 表结构固定：列 `ts_code`、`trade_date`、`suspend_timing`、`suspend_type`，主键 `(ts_code, trade_date, suspend_type)`，附加索引 `trade_date`。
- 写入方式固定为 `DELETE FROM suspend_d WHERE trade_date = <水位>` + `executemany` 批量 INSERT；**不用** `INSERT OR REPLACE`，**不用** `ON CONFLICT`。
- 每段的行**先按 `trade_date` 升序排列**再写入（理由见 spec 第 4 节）。
- 显式传 `fields='ts_code,trade_date,suspend_timing,suspend_type'`；**不传** `suspend_type`。
- 不额外引入事务管理，不做失败回滚，不加「返回 0 行」「返回行数恰好 5000」之类的护栏。
- 抓取函数的可注入参数只有 `today: date | None`；重试走既有的 `call_with_retry`，不新增重试逻辑。
- 开发测试使用 `python -m unittest`，全部用假客户端，不联网。

## Review Focus

- **表为空时首次运行**：`SELECT MAX(trade_date)` 返回 `None`，此时**不能**执行 DELETE、不能崩，起点必须是运行日往前 11 年。
- **水位那天可能是上次拉到一半的**：必须只删这一天、其余日期一行不动；删多了会丢数据。
- **同一天既有停牌又有复牌**：`(ts_code, trade_date)` 会撞主键，主键必须含 `suspend_type`，两行都要能存进去。
- **`suspend_timing` 的空值**：接口给 `null`，pandas 变 `NaN`，要存成 SQL NULL 而不是字符串 `'nan'`。
- **重复运行**：第二次运行后表的内容与行数必须与第一次完全一致，不重复、不报错。

---

### Task 1: `store.py` 加表与两个写入原语

**Files:**
- Modify: `screener/store.py`
- Test: `tests/test_store_raw_tables.py`

**Interfaces:**
- Consumes: 无（`store.py` 是既有模块）。
- Produces:
  - `insert_rows(connection: sqlite3.Connection, *, table: str, columns: tuple[str, ...], rows: Iterable[Sequence[object]]) -> int` —— 只插入，不删任何东西，不提交，返回写入行数。
  - `delete_rows(connection: sqlite3.Connection, *, table: str, column: str, value: object) -> int` —— 删除 `column = value` 的行，不提交，返回删除行数。
  - `replace_rows` 签名与返回值不变，内部改成「`DELETE FROM <表>` + `insert_rows`」，日志行 `"%s 清空旧数据，写入 %d 行"` 原样保留。
  - `open_database` 建出 `suspend_d` 表与 `suspend_d_trade_date` 索引。

- [ ] **Step 1: Write the failing test**

在 `tests/test_store_raw_tables.py` 的 `RawTablesTest` 里加五条（顶部 import 补 `sqlite3` 与 `begin_sync, delete_rows, insert_rows`）：

```python
    def test_suspend_d_table_has_the_decided_columns_and_primary_key(self):
        self.assertEqual(
            [name for name, _ in self._columns("suspend_d")],
            ["ts_code", "trade_date", "suspend_timing", "suspend_type"],
        )
        self.assertEqual([pk for _, pk in self._columns("suspend_d")], [1, 2, 0, 3])

    def test_suspend_d_stores_both_types_on_the_same_day(self):
        insert_rows(
            self.connection,
            table="suspend_d",
            columns=SUSPEND_D_COLUMNS,
            rows=[("000001.SZ", "20261016", "09:30-10:00", "S"), ("000001.SZ", "20261016", None, "R")],
        )

        self.assertEqual(
            self.connection.execute(
                "SELECT suspend_timing, suspend_type FROM suspend_d ORDER BY suspend_type"
            ).fetchall(),
            [("09:30-10:00", "S"), (None, "R")],
        )

    def test_delete_rows_removes_only_the_named_day(self):
        insert_rows(
            self.connection,
            table="suspend_d",
            columns=SUSPEND_D_COLUMNS,
            rows=[
                ("000001.SZ", "20261015", None, "S"),
                ("000001.SZ", "20261016", None, "S"),
                ("000002.SZ", "20261016", None, "S"),
            ],
        )

        removed = delete_rows(self.connection, table="suspend_d", column="trade_date", value="20261016")

        self.assertEqual(removed, 2)
        self.assertEqual(
            self.connection.execute("SELECT ts_code, trade_date FROM suspend_d").fetchall(),
            [("000001.SZ", "20261015")],
        )

    def test_insert_rows_is_repeatable_on_an_empty_day(self):
        rows = [("000001.SZ", "20261016", None, "S")]
        insert_rows(self.connection, table="suspend_d", columns=SUSPEND_D_COLUMNS, rows=rows)
        first = self.connection.execute("SELECT * FROM suspend_d").fetchall()
        delete_rows(self.connection, table="suspend_d", column="trade_date", value="20261016")
        insert_rows(self.connection, table="suspend_d", columns=SUSPEND_D_COLUMNS, rows=rows)

        self.assertEqual(self.connection.execute("SELECT * FROM suspend_d").fetchall(), first)

    def test_abort_commits_the_partial_write_but_a_plain_write_does_not(self):
        self.assertFalse(self.connection.in_transaction)

        batch = begin_sync(self.connection)
        self.assertFalse(self.connection.in_transaction)
        insert_rows(
            self.connection,
            table="suspend_d",
            columns=SUSPEND_D_COLUMNS,
            rows=[("000001.SZ", "20261016", None, "S")],
        )
        self.assertTrue(self.connection.in_transaction)

        batch.abort()
        self.assertFalse(self.connection.in_transaction)

        reopened = sqlite3.connect(self.directory / "screener.sqlite3")
        try:
            self.assertEqual(reopened.execute("SELECT COUNT(*) FROM suspend_d").fetchone(), (1,))
        finally:
            reopened.close()
```

并在文件顶部加常量：

```python
SUSPEND_D_COLUMNS = ("ts_code", "trade_date", "suspend_timing", "suspend_type")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.test_store_raw_tables -v`
Expected: FAIL —— `ImportError: cannot import name 'insert_rows'`

- [ ] **Step 3: Implement in `screener/store.py`**

在 `open_database` 里 `trade_cal` 建表之后加：

```python
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS suspend_d (
            ts_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            suspend_timing TEXT,
            suspend_type TEXT NOT NULL,
            PRIMARY KEY (ts_code, trade_date, suspend_type)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS suspend_d_trade_date ON suspend_d (trade_date)"
    )
```

把 `replace_rows` 改成委托（日志行一个字不改），并在它上面加两个原语：

```python
def insert_rows(
    connection: sqlite3.Connection,
    *,
    table: str,
    columns: tuple[str, ...],
    rows: Iterable[Sequence[object]],
) -> int:
    """只插入，不删任何东西；不提交，提交点仍由同步批次决定。"""
    materialized = list(rows)
    placeholders = ", ".join("?" * len(columns))
    connection.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        materialized,
    )
    return len(materialized)


def delete_rows(
    connection: sqlite3.Connection, *, table: str, column: str, value: object
) -> int:
    """删掉 column = value 的行；不提交，提交点仍由同步批次决定。"""
    cursor = connection.execute(f"DELETE FROM {table} WHERE {column} = ?", (value,))
    logger.debug("%s 删除 %s = %s 的行，共 %d 行", table, column, value, cursor.rowcount)
    return cursor.rowcount
```

`replace_rows` 的新函数体（保持返回值与日志不变）：

```python
    materialized = list(rows)
    connection.execute(f"DELETE FROM {table}")
    count = insert_rows(connection, table=table, columns=columns, rows=materialized)
    logger.debug("%s 清空旧数据，写入 %d 行", table, count)
    return count
```

- [ ] **Step 4: Run the whole suite to verify it passes**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -t .`
Expected: OK（70 tests，`replace_rows` 的既有断言全绿）

- [ ] **Step 5: Commit**

```bash
git add screener/store.py tests/test_store_raw_tables.py
git commit -m "feat: add suspend_d table and insert/delete primitives"
```

---

### Task 2: `sync/suspend_d.py` 抓取模块

**Files:**
- Create: `screener/sync/suspend_d.py`
- Test: `tests/test_sync_suspend_d.py`

**Interfaces:**
- Consumes: `store.insert_rows`、`store.delete_rows`（Task 1）；`sync.retry.call_with_retry(operation, *, sleep=..., attempts=3)`（已存在）。
- Produces:
  - `screener.sync.suspend_d.DATASET = "停复牌"`
  - `screener.sync.suspend_d.COLUMNS = ("ts_code", "trade_date", "suspend_timing", "suspend_type")`
  - `screener.sync.suspend_d.CHUNK_DAYS = 27`、`BACK_YEARS = 11`
  - `fetch_suspend_d(connection: sqlite3.Connection, client: object, *, today: date | None = None) -> int` —— 返回本次写入的总行数。

- [ ] **Step 1: Write the failing test**

新建 `tests/test_sync_suspend_d.py`。假客户端按 `start_date`/`end_date` 过滤自己那批行，并**故意倒序**返回，用来证明写入顺序不依赖接口顺序：

```python
import shutil
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from screener.store import open_database
from screener.sync.suspend_d import COLUMNS, fetch_suspend_d


class FakeClient:
    """按区间过滤、倒序返回的假客户端。"""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def suspend_d(self, **params):
        self.calls.append(params)
        start, end = params["start_date"], params["end_date"]
        picked = [row for row in self.rows if start <= row["trade_date"] <= end]
        return pd.DataFrame(list(reversed(picked)), columns=params["fields"].split(","))


def row(trade_date, **overrides):
    return {
        "ts_code": "000001.SZ",
        "trade_date": trade_date,
        "suspend_timing": None,
        "suspend_type": "S",
        **overrides,
    }


class SuspendDTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        self.connection = open_database(self.directory / "screener.sqlite3")

    def tearDown(self):
        self.connection.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_empty_table_starts_eleven_years_back_and_chunks_by_27_days(self):
        client = FakeClient()

        fetch_suspend_d(self.connection, client, today=date(2026, 10, 18))

        self.assertEqual(client.calls[0]["start_date"], "20151018")
        self.assertEqual(client.calls[0]["end_date"], "20151113")
        self.assertEqual(client.calls[1]["start_date"], "20151114")
        self.assertEqual(client.calls[-1]["end_date"], "20261018")
        self.assertEqual(client.calls[0]["fields"], "ts_code,trade_date,suspend_timing,suspend_type")
        self.assertEqual(len(client.calls), 149)
        self.assertNotIn("suspend_type", client.calls[0])

    def test_water_level_day_is_deleted_and_chunking_starts_there(self):
        client = FakeClient([row("20211001"), row("20211005")])
        fetch_suspend_d(self.connection, client, today=date(2021, 11, 5))
        client.calls.clear()

        fetch_suspend_d(self.connection, client, today=date(2021, 11, 5))

        self.assertEqual(client.calls[0]["start_date"], "20211005")
        self.assertEqual(client.calls[0]["end_date"], "20211031")
        self.assertEqual(
            self.connection.execute(
                "SELECT trade_date FROM suspend_d ORDER BY trade_date"
            ).fetchall(),
            [("20211001",), ("20211005",)],
        )

    def test_rows_are_written_in_trade_date_order(self):
        client = FakeClient([row("20261003"), row("20261001"), row("20261002")])

        fetch_suspend_d(self.connection, client, today=date(2026, 10, 18))

        self.assertEqual(
            self.connection.execute(
                "SELECT trade_date FROM suspend_d ORDER BY rowid"
            ).fetchall(),
            [("20261001",), ("20261002",), ("20261003",)],
        )

    def test_writes_the_rows_and_returns_the_count(self):
        client = FakeClient(
            [row("20261001"), row("20261002", suspend_type="R"), row("20261002", suspend_timing="09:30-10:00")]
        )

        count = fetch_suspend_d(self.connection, client, today=date(2026, 10, 18))

        self.assertEqual(count, 3)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM suspend_d").fetchone(), (3,)
        )

    def test_null_timing_is_stored_as_sql_null(self):
        fetch_suspend_d(self.connection, FakeClient([row("20261001")]), today=date(2026, 10, 18))

        self.assertEqual(
            self.connection.execute("SELECT suspend_timing FROM suspend_d").fetchone(), (None,)
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM suspend_d WHERE suspend_timing IS NULL"
            ).fetchone(),
            (1,),
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.test_sync_suspend_d -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'screener.sync.suspend_d'`

- [ ] **Step 3: Implement `screener/sync/suspend_d.py`**

```python
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta

import pandas as pd

from screener.store import delete_rows, insert_rows
from screener.sync.retry import call_with_retry

logger = logging.getLogger(__name__)

DATASET = "停复牌"
COLUMNS: tuple[str, ...] = ("ts_code", "trade_date", "suspend_timing", "suspend_type")
CHUNK_DAYS = 27
BACK_YEARS = 11
```

函数按 spec 第 4 节的五步写：

1. 读水位 `water = connection.execute("SELECT MAX(trade_date) FROM suspend_d").fetchone()[0]`。
2. `water is None` 时 `start = end.replace(year=end.year - BACK_YEARS)`，**不调 `delete_rows`**；否则 `start = datetime.strptime(water, "%Y%m%d").date()` 并调 `delete_rows(connection, table="suspend_d", column="trade_date", value=water)`。
3. `while chunk_start <= end:` 循环，`chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), end)`；每轮先把两个日期格式化成 `"%Y%m%d"` 存进局部变量，再用它们构造 `call_with_retry(lambda: client.suspend_d(start_date=..., end_date=..., fields=",".join(COLUMNS)))`（照抄 `trade_cal.py` 的 lambda 写法，避免闭包读到下一轮的值）。
4. 把返回的 `frame` 转成行：`selected = frame[list(COLUMNS)]`、`ordered = selected.sort_values("trade_date")`、`rows = ordered.astype(object).where(ordered.notna(), None)`，然后 `written += insert_rows(connection, table="suspend_d", columns=COLUMNS, rows=rows.itertuples(index=False, name=None))`。
5. `chunk_start = chunk_end + timedelta(days=1)`；循环结束后 `return written`。

日志照 `trade_cal.py` 的样式：每段 DEBUG 记 `"suspend_d 请求：%s ~ %s"` 与 `"suspend_d 返回 %d 行，耗时 %.1f 秒"`；水位那天被删时 DEBUG 记一行（`delete_rows` 自己也会记）。

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m unittest tests.test_sync_suspend_d -v`
Expected: OK（5 tests）

- [ ] **Step 5: Commit**

```bash
git add screener/sync/suspend_d.py tests/test_sync_suspend_d.py
git commit -m "feat: fetch suspend_d by 27-day chunks from the water level"
```

---

### Task 3: 登记数据名并接进联调策略

**Files:**
- Modify: `screener/sync/registry.py`
- Modify: `screener/strategies/mock_all_datasets.py`
- Modify: `tests/tushare_fakes.py`
- Test: `tests/test_sync_registry.py`、`tests/test_strategies.py`

**Interfaces:**
- Consumes: `suspend_d.DATASET`、`suspend_d.fetch_suspend_d`（Task 2）。
- Produces:
  - `registry.DATASET_FETCHERS["停复牌"] is fetch_suspend_d`。
  - `MockAllDatasetsStrategy.spec.required_datasets == ("股票列表", "交易日历", "停复牌")`。
  - `tests.tushare_fakes.FakeTushareClient(suspend_d_rows=())`，其 `suspend_d(**params)` 与既有两个方法一样按 `fields` 裁列。

- [ ] **Step 1: Write the failing test**

`tests/test_sync_registry.py`：给 `TRADE_CAL_ROWS` 旁边加

```python
SUSPEND_D_ROWS = [
    {"ts_code": "000001.SZ", "trade_date": "20261016", "suspend_timing": None, "suspend_type": "S"}
]
```

`fake_client()` 改成 `FakeTushareClient(stock_basic_rows=[stock_basic_row()], trade_cal_rows=TRADE_CAL_ROWS, suspend_d_rows=SUSPEND_D_ROWS)`；`test_dispatches_every_registered_dataset` 改成

```python
    def test_dispatches_every_registered_dataset(self):
        counts = fetch_datasets(self.connection, ("股票列表", "交易日历", "停复牌"), fake_client())

        self.assertEqual(counts, {"股票列表": 1, "交易日历": 1, "停复牌": 1})
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM suspend_d").fetchone(), (1,))
```

`test_logs_progress_for_every_dataset` 的数据集元组同步加上 `"停复牌"`，并在期望的日志列表尾部补两行 `"INFO:screener.sync.registry:开始拉取 停复牌"` 与 `"INFO:screener.sync.registry:停复牌 完成：1 行"`。

`tests/test_strategies.py`：`test_mock_strategy_registers_every_registered_dataset` 里那句 `self.assertEqual(mock.spec.required_datasets, tuple(DATASET_FETCHERS))` **不用改** —— 它就是用来在漏改时报警的。

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.test_sync_registry tests.test_strategies -v`
Expected: FAIL —— `SyncError: 未登记的数据名：停复牌`，且 `TypeError: ... unexpected keyword argument 'suspend_d_rows'`

- [ ] **Step 3: Implement**

`tests/tushare_fakes.py`：`FakeTushareClient.__init__` 加 `suspend_d_rows=()` 并存起来，加方法。**必须按 `start_date`/`end_date` 过滤**——真实接口就是按 `trade_date` 区间过滤的；不过滤的话，11 年回填那 149 次调用每次都会返回同一行，第二段就撞主键了。

```python
    def suspend_d(self, **params):
        fields = self._record("suspend_d", params)
        picked = [
            row
            for row in self.suspend_d_rows
            if params["start_date"] <= row["trade_date"] <= params["end_date"]
        ]
        return self._slice(picked, fields)
```

`screener/sync/registry.py`：顶部加两行 import（照 `stock_basic` 的样式），`DATASET_FETCHERS` 加一项

```python
    SUSPEND_D_DATASET: fetch_suspend_d,
```

`screener/strategies/mock_all_datasets.py`：`required_datasets` 改成 `("股票列表", "交易日历", "停复牌")`。

- [ ] **Step 4: Run the whole suite to verify it passes**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -t .`
Expected: OK（75 tests）

- [ ] **Step 5: Commit**

```bash
git add screener/sync/registry.py screener/strategies/mock_all_datasets.py tests/tushare_fakes.py tests/test_sync_registry.py
git commit -m "feat: register the 停复牌 dataset and cover it with the mock strategy"
```

---

### Task 4: 同步既有文档

**Files:**
- Modify: `docs/data-model-proposal.md`（D8 的增量表）
- Modify: `docs/superpowers/specs/2026-10-11-tech-selection-architecture-design.md`（「同步」表）
- Modify: `product.md`（菜单示意）
- Modify: `docs/tushare-api-evaluation.md`（`suspend_d` 小节）

**Interfaces:**
- Consumes: spec 第 8 节的五条。
- Produces: 无代码接口；改完 `docs/data-model-proposal.md` 与架构文档对 `suspend_d` 的说法与本轮实现一致。

- [ ] **Step 1: 改 D8 的增量表**

在 `docs/data-model-proposal.md` 的增量策略表里加一行，与既有行同格式：

```
| 停复牌：`suspend_d` | `MAX(trade_date)` | 删掉水位那一天，再从该天起按 **27 天**固定窗口向今天补；每段的行按 `trade_date` 升序写入 | 首次约 149 次；日常 1～2 次 |
```

- [ ] **Step 2: 改架构文档的「同步」表**

`docs/superpowers/specs/2026-10-11-tech-selection-architecture-design.md` 的同步表里加同一行（措辞与本文件既有行一致），并在「回补窗口由各数据集各自决定」那条后面补一句：`suspend_d` 的回补是 1 天（只删水位那天）。

- [ ] **Step 3: 改 `product.md` 的菜单示意**

「更新范围」示例加上「停复牌」，并说明首次更新因为要做 11 年回填会明显变慢（约 149 次调用）。

- [ ] **Step 4: 改 `docs/tushare-api-evaluation.md`**

`suspend_d` 小节补三条实测并修正一处表述：区间超过约 1 个月会被静默截断到恰好 5000 行；11 年区间报 `date_range_too_large`；官方文档没有分页参数，按日期分段是唯一出路；`suspend_timing` 的官方口径是「日内停牌才有值，否则为空」，不是「恒为 null」。

- [ ] **Step 5: 检查与提交**

Run: `.venv\Scripts\ruff.exe format --check --no-cache .`
Expected: 全部已格式化（本轮不动代码，只确认没有顺带改坏）

```bash
git add docs/data-model-proposal.md docs/superpowers/specs/2026-10-11-tech-selection-architecture-design.md product.md docs/tushare-api-evaluation.md
git commit -m "docs: record suspend_d in the incremental strategy tables"
```
