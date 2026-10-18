# 原始层首批接口（股票列表与交易日历）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpower-subagent-driven-development (recommended) or superpower-executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让菜单的「更新本地数据」真的调用 Tushare，把 `stock_basic`（在市股票）和 `trade_cal`（交易日历）写进 SQLite，重复运行结果一致。

**Architecture:** `screener/sync.py` 拆成 `screener/sync/` 包，一个接口一个模块；`store` 负责建表与"删空 + 批量插入"；`sync/registry.py` 把中文数据名映射到抓取函数，`sync/pipeline.py` 的 `sync_all` 负责读配置、建客户端、开批次、记账、发布。筛选流程一行不改。

**Tech Stack:** Python 3.13、标准库 `sqlite3` 与 `unittest`、已安装的 `tushare` SDK、`pandas.DataFrame`。不新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-18-raw-layer-stock-basic-trade-cal-design.md`

## Global Constraints

- 本轮只接 `trade_cal` 与 `stock_basic` 两个接口；其余 6 个必需接口不做。
- 代理地址固定为 `https://tl.kaixin8.top/tushare/sdk`；客户端构造顺序固定为 `ts.pro_api(token, timeout=120)`，随后 `client._DataApi__http_url = <代理地址>`。
- 只有 `screener/sync/client.py` 可以 `import tushare` 或设置该地址。`screener/strategies/` 与 `screener/report.py` 的源码不得出现 `tushare`。
- Key 只从 `TUSHARE_TOKEN` 读取，不写入代码、SQLite、HTML、日志或 Git。
- 列名照抄接口字段、字段全部入库（D1）；日期一律 TEXT `YYYYMMDD`（D10）；原始层不存派生指标（D2）。
- 写入方式固定为 `DELETE FROM <表>` + `executemany` 批量插入，不用 `INSERT OR REPLACE`，不用 `ON CONFLICT`。
- 不引入事务，不做失败回滚，不加"返回 0 行""接近上限"之类的异常护栏。中间出错就下次更新重跑一遍。
- 策略文件只登记中文数据名（`股票列表`、`交易日历`），不出现 Tushare 接口名。
- 开发测试使用 `python -m unittest`，全部用假客户端，不联网。

## Review Focus

- **接口在 HTTP 层出错时，SDK 会静默返回空表**（`tushare/pro/client.py` 第 43 行是 `if res:`），"删空 + 插入"会把 `stock_basic` 清空而更新仍显示成功。设计已明确接受这个风险、不设护栏；这条列在这里是为了让使用者知道，不是待办。
- **第一次更新时表是空的**：`DELETE FROM` 无行可删，不能因此报错，插入后必须真的落库。
- **字段缺失的两种形态**：接口对空文本返回空串（`pretrade_date` 第一行）、对缺失数值返回 `NaN`。空串存空串、`NaN` 存 NULL，不能变成字符串 `'nan'`，也不能因为 `None` 整批插入失败。
- **同一份数据写两次**：表的内容与行数必须完全一致，不能重复、不能残留上一次多出来的行。
- **策略登记了未实现的数据名**：抛 `SyncError` 并让整批失败，不静默跳过。

---

### Task 1: `sync` 改成包

**Files:**
- Create: `screener/sync/__init__.py`
- Create: `screener/sync/client.py`
- Create: `screener/sync/pipeline.py`
- Delete: `screener/sync.py`
- Test: `tests/test_tushare_connection.py`

**Interfaces:**
- Consumes: `screener.config.Settings`、`screener.store.begin_sync`（均已存在）。
- Produces:
  - `screener.sync.SyncError`、`screener.sync.create_tushare_client(settings: Settings) -> object`、`screener.sync.read_tushare_config(settings: Settings) -> tuple[str, str]`、`screener.sync.sync_all(connection, *, datasets: tuple[str, ...], settings: Settings, fetch: Callable, finished_at: str) -> str`、`screener.sync.fetch_not_implemented(datasets: tuple[str, ...], token: str) -> Mapping[str, int]`（临时保留，Task 7 删除）。
  - `create_tushare_client` 传给 `pro_api` 的 `timeout` 是 `120`。

- [ ] **Step 1: Write the failing test**

改 `tests/test_tushare_connection.py`：假 `pro_api` 接受 `timeout` 并记录它；"源码不含 token"的检查从单个文件改成遍历整个包。

```python
def install_fake_tushare(calls: list, timeouts: list | None = None) -> None:
    fake = types.ModuleType("tushare")

    def pro_api(token, timeout=30):
        calls.append(token)
        if timeouts is not None:
            timeouts.append(timeout)
        return FakeApi()

    fake.pro_api = pro_api
    sys.modules["tushare"] = fake


def test_client_requests_a_long_enough_timeout(self):
    calls, timeouts = [], []
    install_fake_tushare(calls, timeouts)

    create_tushare_client(settings())

    self.assertEqual(timeouts, [120])


def test_package_source_does_not_contain_the_token(self):
    token = "TEST-TOKEN-NOT-REAL"
    sources = sorted(Path("screener/sync").glob("*.py"))

    self.assertTrue(sources)
    for path in sources:
        self.assertNotIn(token, path.read_text(encoding="utf-8"))
```

把原 `test_client_uses_configured_token_and_api_url` 里那两行读 `Path("screener/sync.py")` 的断言删掉（已由上面的测试覆盖）。

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_tushare_connection -v`

Expected: FAIL —— `screener/sync` 还是单文件，`Path("screener/sync").glob("*.py")` 取不到文件；`timeouts` 是 `[]`。

- [ ] **Step 3: Create the package**

`client.py`：把 `read_tushare_config` 与 `create_tushare_client` 原样搬过来，只把 `ts.pro_api(token)` 改成 `ts.pro_api(token, timeout=120)`；`import tushare` 保持在函数体内（延迟导入，`tests/test_tushare_connection.py` 靠 `sys.modules` 塞假模块，提到模块顶层会让它失效）。

`pipeline.py`：搬 `SyncError`、`fetch_not_implemented`、`sync_all`；`sync_all` 本任务**签名不变**，只是把 `read_tushare_config` 改成从 `client.py` 导入。

`__init__.py`：只做再导出，不含逻辑。

```python
from screener.sync.client import create_tushare_client, read_tushare_config
from screener.sync.pipeline import SyncError, fetch_not_implemented, sync_all

__all__ = [
    "SyncError",
    "create_tushare_client",
    "fetch_not_implemented",
    "read_tushare_config",
    "sync_all",
]
```

删除 `screener/sync.py`。

- [ ] **Step 4: Run the whole suite to verify it passes**

Run: `python -m unittest discover -s tests -v`

Expected: PASS（`cli.py` 和 `tests/test_sync.py` 用的是 `from screener.sync import ...`，再导出后不需要改）。

- [ ] **Step 5: Commit**

```bash
git add screener/sync screener/sync.py tests/test_tushare_connection.py
git commit -m "Split the sync module into a package and extend the client timeout"
```

---

### Task 2: 两张原始表与批量替换写入

**Files:**
- Modify: `screener/store.py`
- Test: `tests/test_store_raw_tables.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `open_database(path: Path) -> sqlite3.Connection` 额外建 `stock_basic` 与 `trade_cal`。
  - `replace_rows(connection: sqlite3.Connection, *, table: str, columns: tuple[str, ...], rows: Iterable[Sequence[object]]) -> int`：先 `DELETE FROM <table>`，再 `executemany` 插入，返回插入行数；**不 commit**（提交点仍是 `SyncBatch.record` / `publish`）。

- [ ] **Step 1: Write the failing test**

`tests/test_store_raw_tables.py`，`setUp`/`tearDown` 照 `tests/test_sync.py` 的写法用 `test-temp/<测试名>/screener.sqlite3`。

```python
def test_raw_tables_have_the_decided_columns_and_primary_keys(self):
    columns = lambda table: [  # noqa: E731
        (row[1], row[5]) for row in self.connection.execute(f"PRAGMA table_info({table})")
    ]
    self.assertEqual(
        [name for name, _ in columns("stock_basic")],
        ["ts_code", "symbol", "name", "area", "industry", "fullname", "enname",
         "cnspell", "market", "exchange", "curr_type", "list_status", "list_date",
         "delist_date", "is_hs"],
    )
    self.assertEqual([pk for _, pk in columns("stock_basic")], [1] + [0] * 14)
    self.assertEqual(
        [name for name, _ in columns("trade_cal")],
        ["exchange", "cal_date", "is_open", "pretrade_date"],
    )
    self.assertEqual([pk for _, pk in columns("trade_cal")], [1, 2, 0, 0])


def test_replace_rows_keeps_only_the_rows_from_this_call(self):
    replace_rows(self.connection, table="trade_cal",
                 columns=("exchange", "cal_date", "is_open", "pretrade_date"),
                 rows=[("SSE", "20261016", 1, "20261015"),
                       ("SSE", "20261017", 0, "20261016")])
    count = replace_rows(self.connection, table="trade_cal",
                         columns=("exchange", "cal_date", "is_open", "pretrade_date"),
                         rows=[("SSE", "20261018", 0, "20261016")])

    self.assertEqual(count, 1)
    self.assertEqual(
        self.connection.execute("SELECT cal_date FROM trade_cal").fetchall(),
        [("20261018",)],
    )


def test_replace_rows_is_repeatable(self):
    rows = [("SSE", "20261016", 1, "20261015")]
    columns = ("exchange", "cal_date", "is_open", "pretrade_date")
    replace_rows(self.connection, table="trade_cal", columns=columns, rows=rows)
    first = self.connection.execute("SELECT * FROM trade_cal").fetchall()
    replace_rows(self.connection, table="trade_cal", columns=columns, rows=rows)

    self.assertEqual(self.connection.execute("SELECT * FROM trade_cal").fetchall(), first)


def test_replace_rows_stores_empty_string_and_none_as_given(self):
    replace_rows(self.connection, table="trade_cal",
                 columns=("exchange", "cal_date", "is_open", "pretrade_date"),
                 rows=[("SSE", "20150105", 1, "")])
    replace_rows(self.connection, table="stock_basic",
                 columns=("ts_code", "delist_date"),
                 rows=[("000001.SZ", None)])

    self.assertEqual(
        self.connection.execute("SELECT pretrade_date FROM trade_cal").fetchone(), ("",)
    )
    self.assertEqual(
        self.connection.execute("SELECT delist_date FROM stock_basic").fetchone(), (None,)
    )
```

（`test_replace_rows_stores_empty_string_and_none_as_given` 往 `stock_basic` 只写两列，是为了确认部分列插入也能工作；实际抓取会写满 15 列。）

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_store_raw_tables -v`

Expected: FAIL —— `no such table: stock_basic`、`ImportError: cannot import name 'replace_rows'`。

- [ ] **Step 3: Implement the tables and `replace_rows`**

`open_database` 里追加两个 `CREATE TABLE IF NOT EXISTS`：

- `stock_basic`：15 列全部 TEXT，`ts_code TEXT PRIMARY KEY`；另建 `CREATE INDEX IF NOT EXISTS stock_basic_list_status ON stock_basic (list_status)`。
- `trade_cal`：`exchange TEXT NOT NULL`、`cal_date TEXT NOT NULL`、`is_open INTEGER`、`pretrade_date TEXT`，`PRIMARY KEY (exchange, cal_date)`。

`replace_rows` 用 `connection.execute(f"DELETE FROM {table}")` + `connection.executemany(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})", rows)`，返回 `cursor.rowcount`。表名与列名只来自本仓库的常量，不做转义。不 commit。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_store_raw_tables -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add screener/store.py tests/test_store_raw_tables.py
git commit -m "Add the stock_basic and trade_cal tables with replace-style writes"
```

---

### Task 3: 代理错误的重试

**Files:**
- Create: `screener/sync/retry.py`
- Test: `tests/test_sync_retry.py`

**Interfaces:**
- Consumes: `screener.sync.pipeline.SyncError`（直接从该模块导入，走包的再导出会和 `__init__` 形成循环导入）。
- Produces:
  - `RETRYABLE_MARKERS: tuple[str, ...] = ("rate_limited", "upstream_pool_exhausted", "timed out", "connection")`
  - `call_with_retry(operation: Callable[[], T], *, sleep: Callable[[float], None] = time.sleep, attempts: int = 3) -> T`

**等待规则（实现者无法自行决定，照抄）：** 判定用 `str(error).lower()` 是否含 `RETRYABLE_MARKERS` 里的任一项。第 1 次失败后等 `30` 秒（消息含 `rate_limited`）否则等 `5` 秒；第 2 次失败后等 `30` 秒（消息含 `rate_limited`）否则等 `15` 秒；第 `attempts` 次仍失败则抛 `SyncError(f"重试 {attempts} 次后仍失败：{error}")`。消息不含任何 marker 的异常**立即抛出**，不重试。

- [ ] **Step 1: Write the failing test**

```python
def test_rate_limited_waits_thirty_seconds_each_time(self):
    waits, attempts = [], []

    def operation():
        attempts.append(1)
        if len(attempts) < 3:
            raise Exception("[rate_limited] 请求额度已达到限制；建议等待30秒后重试")
        return "ok"

    self.assertEqual(call_with_retry(operation, sleep=waits.append), "ok")
    self.assertEqual(waits, [30, 30])
    self.assertEqual(len(attempts), 3)


def test_upstream_failure_waits_five_then_fifteen(self):
    waits, attempts = [], []

    def operation():
        attempts.append(1)
        if len(attempts) < 3:
            raise Exception("[upstream_pool_exhausted] 数据当前不可用")
        return "ok"

    call_with_retry(operation, sleep=waits.append)

    self.assertEqual(waits, [5, 15])


def test_gives_up_after_three_attempts(self):
    def operation():
        raise Exception("[upstream_pool_exhausted] 数据当前不可用")

    with self.assertRaisesRegex(SyncError, "重试 3 次后仍失败"):
        call_with_retry(operation, sleep=lambda seconds: None)


def test_does_not_retry_an_unknown_error(self):
    waits = []

    def operation():
        raise Exception("[invalid_params] 业务参数缺失或格式不合法")

    with self.assertRaisesRegex(Exception, "invalid_params"):
        call_with_retry(operation, sleep=waits.append)

    self.assertEqual(waits, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_sync_retry -v`

Expected: FAIL —— `No module named 'screener.sync.retry'`。

- [ ] **Step 3: Implement `call_with_retry`**

循环 `attempts` 次调用 `operation()`；捕获 `Exception`，按上面的等待规则决定等待值或立即抛出；每次等待前调用 `sleep(seconds)`。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_sync_retry -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add screener/sync/retry.py tests/test_sync_retry.py
git commit -m "Retry proxy rate limits and upstream failures with backoff"
```

---

### Task 4: 抓取股票列表

**Files:**
- Create: `screener/sync/stock_basic.py`
- Test: `tests/test_sync_stock_basic.py`

**Interfaces:**
- Consumes: `screener.store.replace_rows`（Task 2）、`screener.sync.retry.call_with_retry`（Task 3）。
- Produces:
  - `DATASET: str = "股票列表"`
  - `COLUMNS: tuple[str, ...]`，15 列，顺序与 Task 2 的建表一致。
  - `fetch_stock_basic(connection: sqlite3.Connection, client: object) -> int`：一次调用 `client.stock_basic(exchange="", list_status="L", fields=",".join(COLUMNS))`，写库，返回写入行数。

- [ ] **Step 1: Write the failing test**

假客户端放在测试文件里：

```python
class FakeClient:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def stock_basic(self, **params):
        self.calls.append(params)
        return self.frame


def frame_of(rows):
    return pd.DataFrame(rows, columns=list(COLUMNS))
```

```python
def test_fetches_listed_stocks_in_a_single_call_with_explicit_fields(self):
    client = FakeClient(frame_of([dict.fromkeys(COLUMNS, "")]))

    fetch_stock_basic(self.connection, client)

    self.assertEqual(len(client.calls), 1)
    self.assertEqual(client.calls[0]["list_status"], "L")
    self.assertEqual(client.calls[0]["exchange"], "")
    self.assertEqual(client.calls[0]["fields"], ",".join(COLUMNS))


def test_writes_the_rows_and_returns_the_count(self):
    rows = [
        {**dict.fromkeys(COLUMNS, ""), "ts_code": "000001.SZ", "name": "平安银行", "list_status": "L"},
        {**dict.fromkeys(COLUMNS, ""), "ts_code": "600000.SH", "name": "浦发银行", "list_status": "L"},
    ]

    count = fetch_stock_basic(self.connection, FakeClient(frame_of(rows)))

    self.assertEqual(count, 2)
    self.assertEqual(
        self.connection.execute("SELECT ts_code FROM stock_basic ORDER BY ts_code").fetchall(),
        [("000001.SZ",), ("600000.SH",)],
    )


def test_a_second_run_replaces_the_previous_rows(self):
    fetch_stock_basic(self.connection, FakeClient(frame_of([dict.fromkeys(COLUMNS, "")])))
    fetch_stock_basic(self.connection, FakeClient(frame_of(
        [dict.fromkeys(COLUMNS, ""), dict.fromkeys(COLUMNS, "")])))

    self.assertEqual(
        self.connection.execute("SELECT COUNT(*) FROM stock_basic").fetchone(), (2,)
    )


def test_missing_values_become_null_not_the_string_nan(self):
    rows = [{**dict.fromkeys(COLUMNS, ""), "ts_code": "000001.SZ", "delist_date": float("nan")}]

    fetch_stock_basic(self.connection, FakeClient(frame_of(rows)))

    self.assertEqual(
        self.connection.execute("SELECT delist_date FROM stock_basic").fetchone(), (None,)
    )
```

（`frame_of` 用 `dict.fromkeys(COLUMNS, "")` 造满 15 列，避免列数不符。）

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_sync_stock_basic -v`

Expected: FAIL —— `No module named 'screener.sync.stock_basic'`。

- [ ] **Step 3: Implement `fetch_stock_basic`**

```python
def fetch_stock_basic(connection: sqlite3.Connection, client: object) -> int:
    frame = call_with_retry(
        lambda: client.stock_basic(exchange="", list_status="L", fields=",".join(COLUMNS))
    )
    rows = frame[list(COLUMNS)].astype(object).where(frame.notna(), None)
    return replace_rows(
        connection, table="stock_basic", columns=COLUMNS, rows=rows.itertuples(index=False, name=None)
    )
```

`where(frame.notna(), None)` 是必需的：接口对缺失数值返回 `NaN`，不转换会写进字符串 `'nan'`。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_sync_stock_basic -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add screener/sync/stock_basic.py tests/test_sync_stock_basic.py
git commit -m "Fetch the listed stock snapshot into stock_basic"
```

---

### Task 5: 抓取交易日历

**Files:**
- Create: `screener/sync/trade_cal.py`
- Test: `tests/test_sync_trade_cal.py`

**Interfaces:**
- Consumes: `screener.store.replace_rows`、`screener.sync.retry.call_with_retry`。
- Produces:
  - `DATASET: str = "交易日历"`
  - `COLUMNS: tuple[str, ...] = ("exchange", "cal_date", "is_open", "pretrade_date")`
  - `fetch_trade_cal(connection: sqlite3.Connection, client: object, *, today: date | None = None) -> int`：`today` 默认 `date.today()`；起点是 `today.replace(year=today.year - 11)`，格式 `YYYYMMDD`。

- [ ] **Step 1: Write the failing test**

```python
def test_requests_sse_eleven_years_back_in_one_call(self):
    client = FakeClient(frame_of([]))  # 与 Task 4 同名的假客户端，方法换成 trade_cal

    fetch_trade_cal(self.connection, client, today=date(2026, 10, 18))

    self.assertEqual(len(client.calls), 1)
    self.assertEqual(
        client.calls[0],
        {
            "exchange": "SSE",
            "start_date": "20151018",
            "end_date": "20261018",
            "fields": "exchange,cal_date,is_open,pretrade_date",
        },
    )


def test_writes_the_rows_and_returns_the_count(self):
    rows = [
        {"exchange": "SSE", "cal_date": "20150105", "is_open": 1, "pretrade_date": ""},
        {"exchange": "SSE", "cal_date": "20150106", "is_open": 1, "pretrade_date": "20150105"},
    ]

    count = fetch_trade_cal(self.connection, FakeClient(frame_of(rows)), today=date(2026, 10, 18))

    self.assertEqual(count, 2)
    self.assertEqual(
        self.connection.execute(
            "SELECT cal_date, is_open, pretrade_date FROM trade_cal ORDER BY cal_date"
        ).fetchall(),
        [("20150105", 1, ""), ("20150106", 1, "20150105")],
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_sync_trade_cal -v`

Expected: FAIL —— `No module named 'screener.sync.trade_cal'`。

- [ ] **Step 3: Implement `fetch_trade_cal`**

同 Task 4 的形状：`call_with_retry` 包住 `client.trade_cal(...)`，`frame[list(COLUMNS)]` 做 `NaN → None` 转换后交给 `replace_rows`。日期用 `date.strftime("%Y%m%d")`。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_sync_trade_cal -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add screener/sync/trade_cal.py tests/test_sync_trade_cal.py
git commit -m "Fetch the SSE trading calendar into trade_cal"
```

---

### Task 6: 数据名分发与 `sync_all` 接线

**Files:**
- Create: `screener/sync/registry.py`
- Modify: `screener/sync/pipeline.py`（`sync_all` 建客户端并把它交给 `fetch`）
- Modify: `screener/sync/__init__.py`（再导出 `fetch_datasets`）
- Modify: `tests/test_sync.py`（4 处 `fetch=lambda datasets, token: ...`）
- Modify: `tests/test_cli.py`（`_fetch`）
- Test: `tests/test_sync_registry.py`

**Interfaces:**
- Consumes: `fetch_stock_basic`（Task 4）、`fetch_trade_cal`（Task 5）、`create_tushare_client`（Task 1）。
- Produces:
  - `DATASET_FETCHERS: dict[str, Callable[[sqlite3.Connection, object], int]] = {"股票列表": fetch_stock_basic, "交易日历": fetch_trade_cal}`
  - `fetch_datasets(connection: sqlite3.Connection, datasets: tuple[str, ...], client: object) -> Mapping[str, int]`：逐个数据名查表，未登记抛 `SyncError(f"未登记的数据名：{dataset}")`。
  - `sync_all(connection, *, datasets, settings, fetch, finished_at) -> str` 中 `fetch` 的签名改为 `Callable[[sqlite3.Connection, tuple[str, ...], object], Mapping[str, int]]`；`sync_all` 在 `begin_sync` **之前**调用 `create_tushare_client(settings)`，然后 `fetch(connection, datasets, client)`。

- [ ] **Step 1: Write the failing test**

```python
def test_unknown_dataset_name_raises(self):
    with self.assertRaisesRegex(SyncError, "未登记的数据名：不存在的数据"):
        fetch_datasets(self.connection, ("不存在的数据",), FakeClient())


def test_dispatches_every_registered_dataset(self):
    counts = fetch_datasets(self.connection, ("股票列表", "交易日历"), FakeClient())

    self.assertEqual(set(counts), {"股票列表", "交易日历"})
```

同时把 `tests/test_sync.py` 里 4 处 lambda 与 `tests/test_cli.py` 的 `_fetch` 改成三参：

```python
fetch=lambda connection, datasets, client: {"prices": 1}
def _fetch(self, connection, datasets, client): ...
```

并在 `tests/test_sync.py` 加一条，钉住接线：

```python
def test_fetch_receives_the_connection_datasets_and_a_client(self):
    seen = {}

    def fetch(connection, datasets, client):
        seen.update(connection=connection, datasets=datasets, client=client)
        return {"prices": 1}

    sync_all(self.connection, datasets=("prices",), settings=settings(), fetch=fetch,
             finished_at="2026-10-11 09:00:00")

    self.assertIs(seen["connection"], self.connection)
    self.assertEqual(seen["datasets"], ("prices",))
    self.assertIsNotNone(seen["client"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_sync_registry tests.test_sync tests.test_cli -v`

Expected: FAIL —— `No module named 'screener.sync.registry'`；`test_sync` 的旧 lambda 被以三参调用时报 `TypeError`。

- [ ] **Step 3: Implement `registry.py` and rewire `sync_all`**

`registry.py` 顶部 `from screener.sync.stock_basic import fetch_stock_basic` 与 `from screener.sync.trade_cal import fetch_trade_cal`。`fetch_datasets` 按 `datasets` 顺序逐项处理，返回 `{dataset: count}`。

`pipeline.py`：`sync_all` 里 `token, _ = read_tushare_config(settings)` 换成 `read_tushare_config(settings)`（只为校验，仍要在开批次前抛出）+ `client = create_tushare_client(settings)`，然后 `counts = fetch(connection, datasets, client)`。其余（`missing` 检查、`record`、`publish`、`abort`）不动。

- [ ] **Step 4: Run the whole suite to verify it passes**

Run: `python -m unittest discover -s tests -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add screener/sync tests/test_sync.py tests/test_sync_registry.py tests/test_cli.py
git commit -m "Dispatch datasets by data name and hand the client to fetch"
```

---

### Task 7: 菜单接上真实抓取与策略登记

**Files:**
- Modify: `screener/cli.py`
- Modify: `screener/sync/pipeline.py`、`screener/sync/__init__.py`（删除 `fetch_not_implemented`）
- Modify: `screener/strategies/episode_01_graham.py`
- Modify: `tests/test_strategies.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `screener.sync.fetch_datasets`（Task 6）。
- Produces:
  - `screener.cli.main(..., fetch: Callable = fetch_datasets, ...)`
  - `GrahamStrategy.spec.required_datasets == ("股票列表", "交易日历")`
  - `fetch_not_implemented` 从包中消失。

- [ ] **Step 1: Write the failing test**

改 `tests/test_strategies.py` 第 17 行：

```python
self.assertEqual(spec.required_datasets, ("股票列表", "交易日历"))
```

在 `tests/test_cli.py` 加一条端到端测试，用 `unittest.mock.patch` 顶掉客户端构造，让**默认**的 `fetch` 真的跑一遍。假客户端定义在测试文件里，两个接口各回一行：

```python
from screener.sync.stock_basic import COLUMNS as STOCK_BASIC_COLUMNS


class FakeTushareClient:
    def stock_basic(self, **params):
        return pd.DataFrame([
            {**dict.fromkeys(STOCK_BASIC_COLUMNS, ""), "ts_code": "000001.SZ", "list_status": "L"}
        ])

    def trade_cal(self, **params):
        return pd.DataFrame([
            {"exchange": "SSE", "cal_date": "20261016", "is_open": 1, "pretrade_date": "20261015"}
        ])
```

```python
def test_update_uses_the_real_fetcher_by_default(self):
    client = FakeTushareClient()
    with mock.patch("screener.sync.pipeline.create_tushare_client", return_value=client):
        code = main(
            input_fn=iter(["1", "1", ""]).__next__,
            output_fn=self.output.append,
            connection=self.connection,
            settings=settings(),
            clock=lambda: "2026-10-11 09:00:00",
            strategies=(GrahamStrategy(),),
            output_root=self.root,
        )

    self.assertEqual(code, 0)
    self.assertEqual(published_datasets(self.connection), frozenset({"股票列表", "交易日历"}))
    self.assertNotIn("SECRET-TOKEN-VALUE", "\n".join(self.output))
```

（这条测试不能注入 `fetch`，否则测不到默认值；`patch` 的靶子是 `screener.sync.pipeline.create_tushare_client`，所以 `pipeline.py` 必须在模块顶层把它导入自己的命名空间。）

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_strategies tests.test_cli -v`

Expected: FAIL —— `required_datasets` 仍是 `()`；默认 `fetch` 还是 `fetch_not_implemented`，菜单报"Tushare 同步尚未实现"。

- [ ] **Step 3: Rewire the menu and delete the placeholder**

`cli.py` 第 11 行的导入换成 `from screener.sync import fetch_datasets, sync_all`，第 37 行默认值换成 `fetch: Callable = fetch_datasets`。

从 `pipeline.py` 删除 `fetch_not_implemented`，从 `__init__.py` 的再导出与 `__all__` 里删掉它。

`episode_01_graham.py` 的 `required_datasets=("股票列表", "交易日历")`。

- [ ] **Step 4: Run the whole suite to verify it passes**

Run: `python -m unittest discover -s tests -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add screener/cli.py screener/sync screener/strategies/episode_01_graham.py tests/test_cli.py tests/test_strategies.py
git commit -m "Point the update menu at the real fetcher and register the two datasets"
```

---

### Task 8: 同步 spec

**Files:**
- Modify: `docs/superpowers/specs/2026-10-11-tech-selection-architecture-design.md`

**Interfaces:**
- Consumes: 本计划 Task 1～7 的结论。
- Produces: spec 与本设计文档一致，章节编号不变。

- [ ] **Step 1: 改「代码组织」**

目录树里 `│   ├── sync.py             # 唯一可以联网的部分` 换成 `sync/` 包（`__init__.py`、`client.py`、`pipeline.py`、`registry.py`、`retry.py`、`stock_basic.py`、`trade_cal.py`）。模块职责表里 `sync` 一行的"唯一可以联网的部分"改为落在 `sync/client.py`。

- [ ] **Step 2: 改第 57 行**

删掉"已经写入的部分新数据不能与旧数据混合后被筛选读到"，改成明确接受失败后表内数据残缺、靠下次更新重跑覆盖。同段"使用数据库事务还是先写入临时库，留到实现时决定"一并删掉（已决定不引入事务）。

- [ ] **Step 3: 补主键表**

「数据层设计」的主键表加一行：

```markdown
| `trade_cal` | `(exchange, cal_date)` | — |
```

- [ ] **Step 4: 验证引用没断**

Run: `rg -n "第 [0-9]" docs/data-model-proposal.md`

Expected: 引用的章节号仍指向同一节（本任务没有增删章节，只是改内容）。

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-10-11-tech-selection-architecture-design.md
git commit -m "Align the architecture spec with the sync package and trade_cal"
```
