# 技术选型与架构

> 日期：2026-10-11。本文只记录第一版的技术选型和代码架构，不包含可运行代码。产品行为以 [product.md](../../../product.md) 为准，策略公式以 [strategies.md](../../../strategies.md) 为准。本文不改变这两份文档已经确定的内容。

## 已确定的选型

- **语言：**Python。本机已有 Python 3.13，Tushare 官方接口也使用 Python。
- **本地数据：**一个 SQLite 数据库文件。程序使用 Python 自带的 `sqlite3` 访问它，不安装独立数据库服务，也不使用 SQLAlchemy。
- **筛选计算：**使用 `pandas`。它已随 Tushare 安装。季度滚动、分红去重和分组筛选写成 Python 函数，不写成一长串 SQL。
- **HTML：**使用 Python 标准库直接生成。页面结构已由产品需求固定，不引入 Jinja2。

筛选和修改指标后的重算只读取 SQLite。只有“更新本地数据”可以联网。

## 明确留到实现时决定

各表的具体列清单、除主键外的索引细节、各接口的更新顺序，以及一次重算的具体耗时，都在实现第 1 集时决定（数据分层、保存范围与各表主键已定，见「数据层设计」）。SQLite 保存 4 年或 10 年数据使用的是同一套技术，因此数据保留期限不属于本次选型。

## 代码组织

第一版是一个本机 Python 命令行程序。Python 不要求像 Java 那样把源码放进 `src/`；第一版直接运行，不加这层目录。包放在仓库根目录的 `screener/`，根目录的 `main.py` 是启动入口，使用 `python main.py` 进入菜单。目录如下：

```text
investor-strategy-screener/
├── main.py                 # 启动入口
├── screener/
│   ├── __init__.py
│   ├── cli.py              # 菜单
│   ├── sync.py             # 唯一可以联网的部分
│   ├── store.py            # SQLite
│   ├── report.py           # HTML
│   └── strategies/
│       ├── __init__.py     # 策略登记
│       ├── base.py         # 策略必须提供的三项
│       └── episode_01_graham.py
├── tests/
│   ├── __init__.py
│   └── test_framework_smoke.py
└── .gitignore              # 忽略 outputs/ 和数据库文件
```

程序仍只分成五块：

| 模块 | 职责 | 不允许做的事 |
| --- | --- | --- |
| `cli` | 主菜单和两个二级菜单 | 不直接调用 Tushare，不计算策略指标 |
| `sync` | 唯一可以联网的部分；调用已安装的 Tushare，把远程数据同步进 SQLite。命令行菜单仍称“更新本地数据” | 不执行筛选，不生成 HTML |
| `store` | 打开 SQLite、建立和升级表结构 | 不认识具体策略公式 |
| `strategies` | 每集一个策略文件，条件直接写在代码里；第 1 集为 `episode_01_graham.py`。`base.py` 规定每个策略必须登记所需数据、菜单名称和结果展示字段 | 不调用 Tushare，不生成 HTML |
| `report` | 用标准库生成离线 HTML | 不重新计算指标，不访问远程数据 |

新增一套策略时，只增加一个策略文件，并登记三项：所需数据、菜单名称、结果展示字段。`cli` 按登记的菜单名称显示已实现策略，`report` 按登记的展示字段输出。这两块不需要认识格雷厄姆或其他具体策略。共用数据在同一次更新中只拉取一次，不为每套策略复制一份数据库。尚未实现的策略不进入菜单，也不纳入更新范围。

## 数据流

“更新本地数据”读取所有已实现策略登记的数据需求，拉取共用数据和必要的专用数据，全部写入 SQLite。只有整次更新成功，才记录这次成功结束的时间，并使新数据对筛选可见。

更新中途失败时，筛选必须继续看到上一次完整成功的数据。已经写入的部分新数据不能与旧数据混合后被筛选读到。使用数据库事务还是先写入临时库，留到实现时决定，但这个可见性要求不能改变。

“运行选股”先做数据完整性检查：确认所选策略和所选日期需要的数据已经完整更新。某一类必需数据缺失或只拉到一半时，命令报错并停止，不生成 HTML。这个检查只判断输入数据是否完整，不对筛选结果做程序化验收。

检查通过后，筛选只用 `sqlite3` 读取 SQLite，用 `pandas` 按策略代码计算，再由 `report` 写 HTML。筛选过程中不联网。筛选正常完成但没有股票入选时，HTML 明确显示“本次无符合条件的股票”。本地基础数据不可用而不能完成筛选时，属于上面的完整性错误，不能生成看似成功的空名单。

SQLite 保存从 Tushare 取回的原始数据，例如行情、财报、分红和交易日历，同时记录表结构版本和每次完整成功更新的结束时间。程序不提前保存格雷厄姆数或其他策略计算结果。修改代码中的阈值后，使用同一份原始数据重新计算。

原始数据的保存范围已定：**接口返回的字段全部入库**（不裁列），**下载不做行级取舍**（全市场，含已退市股票），**历史起点 11 年**（覆盖后续策略所需的 10 年复合增速并留缓冲）。首次下载覆盖全区间，日常更新只补差集——从库里已保存的范围推出缺什么，不重复全量拉取。原始层只存事实，字段取舍与口径判断都留在筛选计算中。逐条决议与接口实测见 [docs/data-model-proposal.md](../../data-model-proposal.md) 与 [docs/tushare-api-evaluation.md](../../tushare-api-evaluation.md)。

## 数据层设计

数据分三层：**原始层（通用）→ 计算层（按策略）→ 展示层（按策略）**。联网只发生在原始层，计算与展示都只读 SQLite。

```text
Tushare ──同步（联网）──▶ 原始层（通用，store）──筛选（只读）──▶ 计算层（按策略，strategies/）──▶ 展示层（按策略，report）──▶ HTML
```

### 原始层

- **一个接口一张表**，列名照抄接口返回的字段名；**接口返回的字段全部入库，不裁列**。
- 原始层**不存派生指标**（PE_TTM、PB、股息率、净现比、分位等一律在计算层现算），也**不建策略结果表**。
- 缺失语义（例如"某年没有分红记录等于 0"）由计算层判断，原始层不补默认值。
- 策略文件只登记**数据名**；"数据名 → 接口"的映射归 `sync`/`store`。一次更新拉取的是**所有已实现策略登记需求的并集**，不为每套策略复制一份数据。

各表主键（财务表的同一报告期会返回多个修订版本，主键必须容纳）：

| 表 | 主键 | 附加索引 |
| --- | --- | --- |
| `daily_basic`、`daily`、`adj_factor` | `(ts_code, trade_date)` | `trade_date` |
| `income`、`balancesheet`、`cashflow` | `(ts_code, end_date, report_type, ann_date, update_flag)` | `end_date` |
| `fina_indicator` | `(ts_code, end_date, ann_date, update_flag)` | `end_date` |
| `dividend` | `(ts_code, end_date, ann_date, div_proc)` | `ann_date` |
| `stock_basic` | `ts_code` | `list_status` |
| `suspend_d` | `(ts_code, trade_date, suspend_type)` | `trade_date` |
| `namechange` | `(ts_code, name, start_date)` | — |
| `index_member_all` | `(ts_code, l1_code, l2_code, l3_code, in_date)` | `ts_code` |

- **不接入 `stock_st`**（2026-10-18）：所有策略都排除 ST/\*ST，不需要单独维护 ST 名单；ST 直接从 `stock_basic.name` 前缀识别（实测同日与 `stock_st` 的 201 只完全一致）。`stock_basic` 是当前快照，历史筛选日的 ST 状态需靠 `namechange` 还原。

- 同一报告期的**多个修订版本全部保留**，"取哪一行"是计算层的口径，不在写入时决定。实测财务表**没有"更新时间"字段**，`update_flag` 的语义未定论，因此不能靠某一列挑"最新"。
- 写入必须**幂等**：主键冲突不报错、不重复，不允许默默覆盖。

### 同步（“更新本地数据”）

- **不写专门的“断点续传”功能**：每次更新读库推出已完成的水位，拉“目标区间 − 已完成区间”的差集。中断只是水位没推进，重跑自然接着补。因此 `sync` 里没有“找断点 / 接着跑”的分支，只有“算差集 → 循环 → 记水位”。
- **重跑不从起点全量重拉**；**不实现负结果缓存**（不记“已尝试但无数据”的股票），换取 `sync` 更简单。
- 每次更新固定重拉**最近 7 天**的披露名单与日频数据，用于捕获迟报与财报更正。

| 要拉的数据 | 水位 | 增量方式 |
| --- | --- | --- |
| 日频：`daily_basic`、`daily`、`adj_factor` | `MAX(trade_date)` | 用交易日历定出缺失交易日，逐日补（1 次/天） |
| 分红：`dividend` | `MAX(ann_date)` | 按 `ann_date` 逐日取全市场 |
| 财务四表 | 逐股已取到的报告期 | `disclosure_date(actual_date=当日)` 取当天披露名单，再逐股拉 |
| 状态类：`stock_basic`、`namechange` | 快照 | 每次全量重拉 |

- **首次全量回填**：11 年 × 4 张财务表 × 5577 只 ≈ 24.5 万次调用 ≈ 20 小时（财报单次区间上限 1 年，必须按年分段），`adj_factor` 约 2700 次 ≈ 13 分钟。日常增量平时 1～2 分钟，披露季峰值约 25 分钟。

### 数据库

- **不做迁移准备**：不为了“将来可能换数据库”限制写法，可以用 `INSERT OR REPLACE`、`ON CONFLICT ... DO UPDATE` 等 SQLite 特有写法。SQLite 性能已实测满足（418 万行全表扫描 1.47 秒）。
- 注意 `INSERT OR REPLACE` 是“删除 + 插入”，**未给出的列会被清成 NULL**，所以要么带全字段，要么用 `ON CONFLICT(主键) DO UPDATE SET 列 = excluded.列`。
- 日期统一存 TEXT `YYYYMMDD`。

逐条决议、实测数据与证据见 [docs/data-model-proposal.md](../../data-model-proposal.md) 与 [docs/tushare-api-evaluation.md](../../tushare-api-evaluation.md)。

## 运行约束

- 菜单一次只执行更新或筛选中的一件事，避免同时写 SQLite。
- Tushare Token 只从本机环境变量读取，不写入代码、SQLite、HTML 或日志。
- SQLite 数据库文件和 `outputs/` 不提交到 Git。
- “上一交易日”从本地保存的交易日历得到，筛选时不再联网。
- 公式可以用 Python 自带的单元测试做开发自查。测试通过不等于验收；策略是否通过只由用户人工决定。
- 第一版不增加网页服务、阈值配置文件、Docker、独立数据库服务或其他框架。
