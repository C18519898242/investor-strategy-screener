# 日志系统设计（2026-10-18）

## 1. 目标

**做什么**：给整个程序装一套日志。要打的地方写一行 `logger.info("...")` / `logger.debug("...")`，框架同时往**控制台**和**日志文件**两个输出源写。控制台看到进度，文件里留全量细节，出问题时能回看。

**为什么现在做**：现在同步期间只有两行输出（「正在更新共用数据……」和「更新完成：…」），中间拉了什么、多少行、有没有重试，全看不到。2026-10-18 排查 `invalid_params` 时只能临时写探针脚本，程序自己没留下任何线索。

**成功标准**：

1. 同步时逐个数据集能看到「开始拉取 X」→「X 完成：N 行」
2. 重试发生时控制台能看见（否则最长 30 秒的退避期间看起来像卡死）
3. 跑完留下 `logs/screener.log`，里面能还原每次 API 调用的接口名、参数、行数、耗时，以及完整异常堆栈
4. 日志文件里**不出现 token**

## 2. 为什么用标准库 `logging`

它是 Python 里最稳、最流行、也是唯一零依赖的日志系统：属于标准库，不会因为第三方包停更或版本冲突而坏掉；本身就是照 log4j 1.x 设计的（log4j 的 Logger / Appender / Layout / Level 四件套，Python 叫 logger / handler / formatter / level）。项目一直只用标准库 + pandas / tushare / pydantic-settings，这套正好容得下，不新增依赖。

第三方 `loguru` API 更短，但要多一个依赖，不采纳。

## 3. 概念模型

**级别是消息的属性**，不是输出源的分家规则。打的时候只问「这件事多重要」，一行代码只调一个级别。

**每个输出源各自有一个接收门槛**（log4j 里叫 appender 的 `threshold`；Python 的 API 复用了 `setLevel` 这个名字，语义是「低于这个级别的消息我不要」）。

```python
logger = logging.getLogger("screener")   # 按模块名分层：screener.sync.registry …
logger.setLevel(logging.DEBUG)           # 对应 log4j <logger level="debug">
file_handler.setLevel(logging.DEBUG)     # 对应 FILE 的 threshold
console_handler.setLevel(logging.INFO)   # 对应 CONSOLE 的 threshold
```

| 调用 | 消息级别 | 文件（门槛 DEBUG） | 控制台（门槛 INFO） |
| --- | --- | --- | --- |
| `logger.debug(...)` | DEBUG | 打 | 丢（低于门槛） |
| `logger.info(...)` | INFO | 打 | 打 |
| `logger.warning(...)` | WARNING | 打 | 打 |
| `logger.error(...)` | ERROR | 打 | 打 |

两个门槛都是**配置**，不是代码：想在控制台也看细节就把门槛降到 DEBUG，想少写文件就提到 INFO，日志调用一行都不用改。

## 4. 两个输出源

| | 文件 | 控制台 |
| --- | --- | --- |
| 路径 / 出口 | `logs/screener.log`（追加，不轮转） | 注入的 `output_fn` |
| 门槛 | `DEBUG` | `INFO` |
| 格式 | `2026-10-18 14:02:34 INFO  screener.sync.registry  开始拉取 股票列表` | 只打消息正文：`开始拉取 股票列表` |
| 异常堆栈 | 打 | **不打** |

- 控制台不打时间戳和 logger 名：它是给人看的进度，混在现有菜单文本里会很脏。
- 控制台**通过 `output_fn` 输出**（不是直接写 stdout），这样菜单文本和进度共用同一条有序通道，现有测试断言 `output_fn` 的写法继续成立。
- 控制台**不区分级别**（不打 `INFO`/`WARNING` 前缀）：消息正文自己说明情况。
- **控制台上每条信息只出现一次**：凡是日志行取代了现有 `output_fn(...)` 的地方，那一行现有输出就删掉，不重复打。涉及 `cli.py` 的三处，见 §5。
- **异常堆栈只进文件**：错误在控制台只留一行 `更新失败：…`，完整堆栈进文件。实现上控制台 handler 先复制一份 record 再清掉 `exc_info`/`exc_text` —— `Formatter` 会把 `exc_text` 缓存在 record 上，不清就会两个源都带上堆栈。
- 文件 handler 显式 `encoding="utf-8"`：Windows 上不给这个参数中文会坏。
- **不轮转**（业主 2026-10-18 决定：一个系统级文件就够）。文件会一直长；将来嫌大就换成 `RotatingFileHandler`，换的是一个 handler，别处不用动。

## 5. 事件清单

级别按「这件事多重要」定，收不收由上面两个门槛决定。

### INFO（进度，控制台可见）

| 位置 | 消息 |
| --- | --- |
| `cli.main` 进入主循环 | `启动：投资人选股策略` |
| `cli._sync_menu` 选定更新 | `更新范围：股票列表、交易日历`（**取代** `cli.py:77` 那句笼统的「所有已实现策略所需的数据」） |
| `sync.registry.fetch_datasets` 每个数据集开始前 | `开始拉取 股票列表` |
| 同上，完成后 | `股票列表 完成：5577 行` |
| `sync.pipeline.sync_all` 全部成功 | `更新完成：2026-10-18 12:36:50`（**取代** `cli.py:90` 那行 `output_fn`，不重复打） |
| `cli._screen_menu` 开始筛选 | `开始筛选：格雷厄姆（2026-10-16）` |
| 同上，完成后 | `筛选完成：入选 0 只，结果 outputs/…`（**取代** `cli.py:142-143` 的 `入选股票：…` 与 `结果文件：…`，两条合并成一条） |

### WARNING（出过问题但自动恢复了，控制台也要看见）

| 位置 | 消息 |
| --- | --- |
| `sync.retry.call_with_retry` 每次重试前 | `第 1 次调用失败（rate_limited），30 秒后重试` |

放 WARNING 而不是 DEBUG 的理由：退避最长 30 秒，控制台看不见的话用户会以为程序卡死了。

### DEBUG（细节，只进文件）

| 位置 | 消息 |
| --- | --- |
| `sync.client.create_tushare_client` | `客户端就绪：timeout=120 秒，地址 …`（**不打 token**） |
| `sync.pipeline.sync_all` 开始 | `开始同步，数据集：股票列表、交易日历` |
| `sync.stock_basic` / `sync.trade_cal` 调用前 | `stock_basic 请求：list_status=L, exchange=, fields=13 个` |
| 同上，调用后 | `stock_basic 返回 5577 行，耗时 1.8 秒` |
| `sync.retry.call_with_retry` 重试成功 | `第 2 次尝试成功` |
| `store.replace_rows` 写入后 | `stock_basic 清空旧数据，写入 5577 行` |

### ERROR（失败，控制台可见，堆栈只进文件）

| 位置 | 消息 |
| --- | --- |
| `cli._sync_menu` 捕获异常 | `更新失败：…`（带 `exc_info`，**取代** `cli.py:88` 那行 `output_fn`） |
| `cli._screen_menu` 捕获 `NotImplementedError` | 沿用现有文案，改走 `logger.error` |

其余提示语（「本地交易日历尚未就绪」「日期格式应为 YYYY-MM-DD」「请先更新本地数据」「本地数据不完整，无法筛选」）是给用户的操作提示，不是事件，仍只走 `output_fn`，不进日志文件。

## 6. 接线

- 新增 `screener/logging.py`：`setup_logging(*, log_file, console)`
  - 用一个模块级变量记住自己装的 handler，下次调用先 `removeHandler` 掉它们 —— 所以反复调用不会累积，也不会让控制台 handler 攥着上一轮的 `output_fn`
  - 确保 `log_file` 的父目录存在（`FileHandler` 自己不建目录）
- `cli.main` 入口调一次：`setup_logging(log_file=log_file, console=output_fn)`
- `main()` 新增参数 `log_file: Path = Path("logs") / "screener.log"`，和现有 `output_root` 一个风格
- **`main.py` 不用改**
- 各模块 `logger = logging.getLogger(__name__)`
- `.gitignore` 加 `logs/`

## 7. token 安全

- 只打 `api_name` 与 `params`。token 是请求体的**顶层字段**，不在 `params` 里（见 `tushare/pro/client.py` 的 `req_params` 结构），按这个口径打不会漏
- 新增测试：跑完一次完整更新后，断言日志文件内容不含 token 值
- 现有三条断言（token 不出现在源码、SQLite 字节、终端输出）全部保留

## 8. 测试

| 层 | 要验证的行为 |
| --- | --- |
| `logging` | 两个 handler 都装上且门槛正确；控制台走注入的函数；文件格式与编码正确；**重复 `setup_logging` 不累积 handler**；异常堆栈只出现在文件里 |
| `sync.registry` | 进度行出现在控制台输出：`开始拉取 股票列表`、`股票列表 完成：1 行` |
| `sync.retry` | 重试前打 WARNING，且控制台能看到 |
| `cli` | 跑完一次更新后日志文件不含 token；现有 `output_fn` 断言继续成立 |

## 9. 文档

- 架构文档加一节「日志」：系统级单文件、控制台 INFO / 文件 DEBUG、不含 token、`logs/` 不提交 Git
- 原始层那份设计文档补一句：同步层在哪几个点打进度与 DEBUG 日志

## 10. 不做（YAGNI）

- 不做日志轮转；不做门槛配置化（门槛写死在 `setup_logging` 里，要改就改代码）
- 不引入 `loguru` / `structlog` 等第三方日志库
- 不做结构化日志（JSON），不接外部日志服务
- 不改 `sync_all` / `fetch_datasets` 的签名。**改动记录（2026-10-18，同日后续）**：`sync_all` 的 `finished_at: str` 已改成 `clock: Callable[[], str]` —— 原来的写法在调用 `sync_all` 之前就把时间算好了，记下的是**开始**时间而不是结束时间，与架构文档「数据流」和 product.md 的「整次更新任务的结束时间」不符；现在由 `sync_all` 在 `batch.publish` 紧前取时间。`fetch_datasets` 的签名始终未动。
- 不把菜单文本、提示语塞进日志文件
