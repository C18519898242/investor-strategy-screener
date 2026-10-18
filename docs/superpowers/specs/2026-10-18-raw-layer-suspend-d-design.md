# 原始层：停复牌（`suspend_d`）设计

> 日期：2026-10-18。本文只记录设计，不含可运行代码。
>
> 上游依据：[技术选型与架构](2026-10-11-tech-selection-architecture-design.md)（「数据流」「数据层设计」「日志」）、[数据建模提案](../../data-model-proposal.md)（D1～D10）、[Tushare 接口实测评估](../../tushare-api-evaluation.md)、[原始层首批接口设计](2026-10-18-raw-layer-stock-basic-trade-cal-design.md)（本轮的体例与约定来源）。
>
> 本轮实测证据：[probe_apis18.py](../../tushare-api-evidence/probe_apis18.py) 与 [probe_out18.txt](../../tushare-api-evidence/probe_out18.txt)、[probe_apis19.py](../../tushare-api-evidence/probe_apis19.py) 与 [probe_out19.txt](../../tushare-api-evidence/probe_out19.txt)（第十九轮被叫停，只留下部分结果，见第 9 节）。
>
> 本文需要同步改动上游文档的地方逐条列在第 8 节。

## 1. 范围与完成标准

**做什么**：把 `suspend_d`（停复牌）接进原始层——菜单「更新本地数据」真的联网调用 Tushare，把接口返回的字段全部写进 SQLite；首次运行做 11 年回填，之后每次更新只补差集。

**完成标准**：

1. 首次运行后 `suspend_d` 表有 11 年数据；紧接着再运行一次，结果不变：不重复、不报错。
2. 日常更新只拉水位之后的数据，调用数在个位数。
3. 筛选流程一行不改。
4. 全部测试用假客户端，不联网。

本轮**只做 `suspend_d`**；`daily_basic` 与其余 4 个必需接口不做。

## 2. 数据名与登记

D6 规定策略文件只登记数据名，不出现接口名。本轮新增一个：

| 数据名 | 对应接口 |
| --- | --- |
| `停复牌` | `suspend_d` |

映射加进 `sync/registry.py` 的 `DATASET_FETCHERS`。

**登记范围由 mock 策略兜住**：`screener/strategies/mock_all_datasets.py`（本轮新增，已落地）登记全部数据集，而 `cli._sync_menu` 算「更新范围」是对**所有策略取并集**，所以「停复牌」会自动进入更新范围，不需要动 Graham 的 `required_datasets`。mock 的数据名是写死的、不 `import sync.registry`（从 strategies 导入 sync 子模块会连带把 tushare 拉进进程，破坏分层），由 `tests/test_strategies.py` 断言它必须等于 registry 的登记表——所以加「停复牌」时这两处都要改，漏改会直接测试失败。

## 3. 一张表

列名照抄接口字段、字段全部入库（D1）；日期一律 TEXT `YYYYMMDD`（D10）；原始层不存派生指标（D2）。

| 表 | 列 | 主键 | 附加索引 |
| --- | --- | --- | --- |
| `suspend_d` | `ts_code`、`trade_date`、`suspend_timing`、`suspend_type` | `(ts_code, trade_date, suspend_type)` | `trade_date` |

- 四列都是 TEXT。
- 主键含 `suspend_type` 是 D3 已定的修正：同一天可能既有停牌（`S`）又有复牌（`R`）两条。实测 `trade_date=20261016` 返回 `{'S': 10, 'R': 1}`，正是这种情况。
- `suspend_timing` 在非日内停牌时接口给 `null`，按 D4 如实存 SQLite 的 NULL。
  - 实现注意：pandas 把 `null` 变成 `NaN`，SQLite 自己会把 `NaN` 存成 NULL，直接写入即可；**不要先把 `NaN` 转成字符串**，那样会存进字面量 `"nan"`。
- 附加索引 `trade_date`：取数时要按它查水位、按它删行。

**写入方式：只删水位那一天，其余靠 INSERT 追加**（不是快照替换）。

```sql
DELETE FROM suspend_d WHERE trade_date = ?;   -- ? = 水位那一天
-- 然后 executemany：INSERT INTO suspend_d (ts_code, trade_date, suspend_timing, suspend_type) VALUES (?, ?, ?, ?)
```

**为什么不是「先删空、再批量插入」**：`stock_basic`/`trade_cal` 是快照，一次调用拿回的就是完整一份，删空是安全的；`suspend_d` 是**事件累积**表，一次只拉一个区间，删空会把库里其余年份全清掉。

**为什么裸 INSERT 就够、不需要 upsert**：重跑只删水位那一天，而水位之后的日期要么根本没写过（崩溃时整批回滚），要么就是被 `abort()` 提交的残片——残片按第 4 节的排序约定也是**连续日期前缀**，所以水位之后最多只有水位那天存在旧行，而它刚好被删掉了。实测重跑补写没有撞主键（见第 4 节）。

## 4. 取数规则

**客户端**：沿用 `ts.pro_api(token, timeout=120)`。

**参数与字段**：显式传 `fields='ts_code,trade_date,suspend_timing,suspend_type'`（项目统一要求显式传 `fields`）。**不传 `suspend_type`**，一次调用同时拿回 `S` 与 `R`——实测 2025 年 `S=3473` + `R=572` = 全部 `4045`。

**分片：固定 27 天**，不按自然月（避免 28/30/31 天的歧义）。

**为什么必须分片**（实测 2026-10-18，证据见 `probe_out18.txt`）：

- 11 年一次调用直接报 `date_range_too_large`。官方文档只写「单次查询返回 5000 行」，**没有写区间长度上限**。
- 区间超过约 1 个月会被**静默截断到恰好 5000 行**：按年分段时 2015～2023 连续 9 年都返回恰好 5000 行；按季度分段时每个季度也都是恰好 5000 行。
- 单个自然月不会被截断：实测最大 7,673 行（2015-11）；2018-01 是 5,017 行，刚好越过 5000 也没被砍。
- 截断边界在「1 个月」与「3 个月」之间。**27 天已实测定案**（2026-10-18，证据见 `probe_out20.txt`）：5 个 27 天区间（月内 2 个、跨月 3 个）全部未触顶 —— 7347、6525、5410、5445、3405 行；跨月同样安全，"阈值按自然月边界"的猜想被推翻。
- 官方文档**没有分页参数**（输入参数只有 `ts_code`/`trade_date`/`start_date`/`end_date`/`suspend_type`），所以按日期分段是唯一出路。
- **自然键唯一性已实测**（2026-10-18，证据见 `probe_out20.txt`）：2015-11（7673 行）、2016-03（6656 行）、2016-04（6639 行）与 20261016（11 行）里，`(ts_code, trade_date, suspend_type)` 的重复数全为 0，再含 `suspend_timing` 也是 0 —— D3 兜底规则要求的实测已满足，主键不加列。

**取数流程**：

1. 读水位 `W = SELECT MAX(trade_date) FROM suspend_d`；表为空时 `W` 取 11 年前（D7 的起点，2015-10-18）。
2. `DELETE FROM suspend_d WHERE trade_date = W`——水位那一天可能是上次拉到一半的。
3. 从 `W` 起**向今天方向**切段，每段 27 天：`[W, W+26天]`、`[W+27天, W+53天]`、……直到今天。
4. 逐段调用接口；每段返回的行**先按 `trade_date` 升序排列**，再批量 INSERT。
5. 全部段写完，`sync_all` 照常记账、发布。

**排序为什么是必需的**：`pipeline.py` 的失败路径会调 `batch.abort()`，而 `abort()` 内部有 `commit()`——所以**被捕获的失败会把中途写的行提交**（实测 2026-10-18：一段写到第 3 行失败，abort 后表里留下 5 行、`MAX(trade_date)` 从 20211003 变成 20211004）。按 `trade_date` 升序写入，这个残片必定是**按日期连续的已写前缀**：水位之前的日期完整，只有水位那天可能缺行。重跑只删水位那天、从它重新拉就够了，实测补写不撞主键。不排序的话，残片可能留下日期空洞（写了 10-01、10-03 却没写 10-02），而水位是最大日期，那个洞永远补不上。

`trade_date` 存 TEXT 不影响排序：`YYYYMMDD` 定宽零填充，字典序就是日期序，`MAX(trade_date)` 同理（实测 2026-10-18）。

**首次回填成本**：11 年 ÷ 27 天 ≈ **149 次调用**。行数没测完（见第 9 节）：已测到的部分（2015Q4～2017Q4 共 9 个季度）合计 **143,829 行**，平均每季度约 1.6 万行，且分布极度偏向早年——2024 全年 3,740 行、2025 年 4,045 行，而 2016 年约 6.5 万行。

**回补窗口 1 天**：只删水位那一天。D8 已改成「回补窗口由各数据集各自决定」，本数据集定为 1 天。

**重试**：沿用既有规则（3 次尝试；`rate_limited` 等 30 秒；`upstream_pool_exhausted`、连接重置、读超时退避 5/15 秒；3 次都失败抛 `SyncError`，整批失败；等待函数可注入，测试里不许真等）。`suspend_d` 实测没有触发过 `rate_limited`。

## 5. 失败语义

**不额外引入事务管理，不做失败回滚，不加异常护栏**：中间出错就下次更新重跑一遍。（SQLite 自己把一次同步包在一个隐式事务里，提交点是 `publish()`，所以崩溃时整批回滚——这是免费得到的，不是我们加的机制。）

- **两条失败路径的落盘状态完全不同**（实测 2026-10-18，脚本 `test-temp/txn_atomicity_check.py`）：
  - **进程崩溃**（没走到 `publish`）：整个同步是一个未提交事务，SQLite 整批回滚，表回到上一次发布的状态，一个字都没变。
  - **被捕获的失败**（`pipeline.py` 捕获异常后调 `batch.abort()`）：`abort()` 内部有 `commit()`，中途写的行**会被提交**，表里留下按日期连续的残片前缀（第 4 节）。
- 两种情况下重跑都只需「删水位那天、从水位接着补」——**断点自愈不需要额外机制**。
- 不因为接口偶发失败做额外处理，也不加「返回 0 行」「返回行数恰好 5000（疑似截断）」之类的检查。**截断是明确接受的静默风险**：27 天分片在实测中从未触顶，但若将来某段触顶，会静默少写该段的部分行且不报错。
- 与快照表的差别：快照表（`stock_basic`/`trade_cal`）被 `abort()` 提交的是「整表的部分行」，下次删空重来；这里提交的是「按日期的前缀」，下次从水位接着补。两者都因为 `abort()` 的提交而可能被筛选读到，而 `published_datasets()` 仍报告上一次成功批次的数据集齐备。这是明确接受的代价。

日志沿用既有约定：`开始拉取 {数据名}` / `{数据名} 完成：{n} 行` 走 INFO、控制台可见；接口请求与返回、写入行数走 DEBUG、只进文件；重试告警走 WARNING、控制台也可见；`更新失败：…` 走 ERROR、堆栈只进文件。日志系统本身的约定见[架构设计](2026-10-11-tech-selection-architecture-design.md)的「日志」节。

## 6. 代码组织

一个接口一个模块，沿用既有约定：

```text
screener/sync/suspend_d.py     # DATASET = "停复牌"、COLUMNS、fetch_suspend_d(connection, client) -> int
```

- `fetch_suspend_d` 内部完成第 4 节的全部步骤，返回写入的行数。
- `store.py` 增加两个与 `replace_rows` 并列的通用原语：`insert_rows`（只插入）与 `delete_rows`（按某一列的值删行）；本轮的组合是「删水位那一天 + 批量插入」，同样**不提交**，提交点仍由同步批次决定。
- `registry.py` 的 `DATASET_FETCHERS` 加一项。
- 表在 `open_database` 里建（含三列主键与 `trade_date` 索引）。

## 7. 接线改动

- `screener/sync/registry.py`：`DATASET_FETCHERS` 增加 `"停复牌"`。
- `screener/strategies/mock_all_datasets.py`：`required_datasets` 增加 `"停复牌"`（不加以防漂移的断言会失败）。
- `cli.py`、`pipeline.py`、`main.py` 不改。
- Graham 的 `required_datasets` 不改。

## 8. 需要同步修改的既有文档

1. **`docs/data-model-proposal.md` D8 的增量表**：增加 `suspend_d` 一行（水位 `MAX(trade_date)`；删水位那天，再从该天起按 27 天分段补；首次约 149 次，日常个位数）。
2. **`docs/superpowers/specs/2026-10-11-tech-selection-architecture-design.md` 的「同步」表**：同样增加 `suspend_d` 一行。
3. **`docs/data-model-proposal.md` D3 主键表**：`suspend_d` 一行已存在且已含 `suspend_type`，**无需改动**——本轮只是落实它。
4. **`product.md`**：更新菜单示意里的「更新范围」示例（会多出「停复牌」），并说明首次更新耗时明显变长。
5. **`docs/tushare-api-evaluation.md`** 的 `suspend_d` 小节：补三条实测——区间超过约 1 个月被静默截断到 5000 行、11 年区间报 `date_range_too_large`、官方无分页参数；并修正「`suspend_timing` 为 `null`」的表述（官方口径是「日内停牌才有值，否则为空」）。

## 9. 测试与验证

全程 TDD，假客户端，不联网。

| 层 | 要验证的行为 |
| --- | --- |
| `store` | `suspend_d` 表能建起来；主键含 `suspend_type`（同一天 `S`+`R` 两行都能存）；`trade_date` 索引存在；「删某一天 + 批量插入」只删那一天、其余行不动；跑两次结果一致（不重复、不报错）；`abort()` 提交的残片状态下重跑（删 `MAX(trade_date)` 那天再补）不撞主键 |
| `pipeline` | `publish()` 之前写入都未提交（`in_transaction` 为真）；`abort()` 会提交已写入的行 |
| `sync/suspend_d` | 表为空时从 11 年前起、按 27 天切段；有水位时只删水位那一天并从该天起切；每段都显式传 `fields`；**写入前按 `trade_date` 升序**（注入乱序的假响应，断言写入顺序有序）；不传 `suspend_type`；返回计数正确 |
| `sync/registry` | 「停复牌」能分发到 `fetch_suspend_d` |
| `strategies` | mock 策略的 `required_datasets` 等于 registry 的登记表（已落地，本轮会因新增数据名而必须同步改） |
| `cli` | 更新流程端到端可用，输出不含 token |

**未测完的数字**：首次回填的总行数。第十九轮探针（`probe_apis19.py`）跑到 2017Q4 被叫停，只留下 9 个季度的部分结果（合计 143,829 行）。这个数字**不影响设计**——分片粒度由截断边界决定，与总行数无关——只在评估耗时与库体积时需要，本轮不追。

## 10. 明确不做

- 不接 `daily_basic` 与其余 4 个必需接口。
- 不做 `suspend_timing` 的语义处理（只按 D1 原样存）。
- 不建水位表（水位从数据表自身推，D8 已定）。
- 不加截断检测护栏（第 5 节，业主既有裁定）。
- 不引入事务、不做失败回滚。
- 不实现负结果缓存（D8 已定）。
- 不改 Graham 的 `required_datasets`。
- 不裁剪字段（D1）。
