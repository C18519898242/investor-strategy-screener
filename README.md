# 投资人选股策略

本项目整理一组视频中的 A 股量化选股规则，供人阅读，也供后续 AI 编写策略代码。当前仓库是**策略规范与核对资料**，尚无可运行的选股程序。

## 从哪里开始

- [产品需求（讨论稿）](product.md)：跨策略的产品目标、本机命令行与 HTML 交付形态，以及修改筛选条件的方式。
- [strategies.md](strategies.md)：主文档。按视频第 1～9 集排列，记录各集的筛选条件、已确定的实现口径和待解决的问题。
- [数据源复核报告](sources/data-source-evaluation.md)：核对第 1 集格雷厄姆策略所需的 Tushare 字段，并说明现成估值指标与本项目定义的差异。
- [第 1 集问题与决定](sources/episode-01-graham/open-questions.md)：记录 Q01～Q12 的已定口径。
- `sources/episode-*/`：已核对视频的转写、画面截图和核对记录。

## 当前进度

| 视频 | 内容 | 状态 |
| --- | --- | --- |
| 第 1 集 | 格雷厄姆 | 已实现，菜单「运行选股」可直接出结果。逐项筛选，任一条件不满足或数据不足就排除该股票；最终生成带 CSS、只含入选股票的 HTML 列表。历史日期按本次运行的本地数据重算。 |
| 第 2～7 集 | 施洛斯、聂夫、巴菲特、费雪、林奇、马克斯 | 已记录视频中的量化条件；尚无逐项视频核对记录和完整实现口径。 |
| 第 8 集 | 戴维斯双击 | 已核对视频；部分指标的计算口径待定。 |
| 第 9 集 | 芒格 | 已核对视频；部分指标的计算口径待定。 |

**使用规则：**`strategies.md` 中的“视频中的量化筛选条件”记录作者的规则；“我们的实现口径”是本项目为写代码选定的定义，不代表作者原意。“视频核对说明”记录差异与未交代事项。未写明的口径不能由实现者自行猜测。第 1 集的口径也不能直接套用于其他集。

## 项目验收

**不设置程序化验收。任何策略是否通过，只由用户人工决定。**验收流程见 [产品需求](product.md#只能由用户人工验收)。

## 数据源

第 1 集按 Tushare Pro 的结构化数据字段开发。[主文档末尾](strategies.md#数据源映射与使用条件tushare-pro-候选)集中列出字段映射、当前核对状态及涉及的章节；标为“候选”的章节尚未确定是否复用同一口径。字段选择、单位换算和记录处理由开发 AI 按接口定义实现，不作为用户待回答的需求。现成估值指标与本项目定义不同的地方仍须按策略公式计算：例如，Tushare 对 `pe_ttm` 的公开说明未明确写出是否采用归母净利润，而其 `pb` 定义与第 1 集选定的市净率分母不同。具体依据见[复核报告](sources/data-source-evaluation.md)。

## 资料目录

```text
product.md                        产品需求与待讨论的产品形态
strategies.md                     九集策略规则与共用数据源映射
scripts/
  backup_db.py                    数据库一致性备份
  income_backfill.py              利润表全历史初始化
  balancesheet_backfill.py        资产负债表全历史初始化
  fina_indicator_backfill.py      财务指标全历史初始化
  dividend_backfill.py            分红送配全历史初始化
sources/
  data-source-evaluation.md       第 1 集的数据源复核
  episode-01-graham/              格雷厄姆视频核对资料
  episode-08-davis/               戴维斯视频核对资料
  episode-09-munger/              芒格视频核对资料
```

视频资料目录中的 `full.srt`、`full.txt`、`full.json` 是自动转写，`frame-*.png` 是画面截图，`verification.md` 是结合口播和画面的核对记录。自动转写未经逐字人工校对，核对阈值时应优先查看画面与核对记录。原始 MP4 不在仓库中，视频格式已由 [.gitignore](.gitignore) 排除。

## 开发环境

项目用 [uv](https://docs.astral.sh/uv/) 准备 Python 环境与依赖。`.python-version` 固定 Python 3.13（本机没有时 uv 会自行下载），`uv.lock` 锁定依赖版本。

把这台电脑或另一台电脑上的仓库准备好后，在项目目录执行下面一条命令即可：uv 会自动创建 `.venv`、安装锁定版本的依赖并启动程序，不需要先手工建环境或装依赖。

```bash
uv run --locked python main.py
```

常用命令（都需要在项目目录执行）：

```bash
uv run --locked python main.py     # 进入菜单
uv run --locked pytest             # 运行全部测试
uv run --locked ruff check .       # 代码检查
uv run --locked ruff format .      # 代码格式化
```

测试用标准库 `unittest` 编写，`pytest` 只是运行器，不改写测试代码。

个人配置（Tushare Token 与请求地址）写在仓库根目录的 `.env`，不提交、每台电脑各自设置一次；模板见 [.env.example](.env.example)。也可以用同名真实环境变量，其优先级高于 `.env`。两项都缺时，更新本地数据会一次报出缺少的名字。

```dotenv
TUSHARE_TOKEN=你的token
TUSHARE_API_URL=https://tl.kaixin8.top/tushare/sdk
```

改动依赖后：编辑 `pyproject.toml`，执行 `uv lock` 更新锁文件，并把 `uv.lock` 一并提交。

## 数据库备份

本地数据库 `data/screener.sqlite3` 用 WAL 模式。直接复制 `data/` 下的文件时，如果库正被写入，主文件与 `-wal` 可能不是同一时刻的状态：轻则丢掉最近的写入，重则备份打不开，而且事后才看得出来。

`scripts/backup_db.py` 用 SQLite 的 `VACUUM INTO` 生成一致性快照，产出独立的单文件备份，默认放在 `data/backup/`。文件名带时间戳，不会覆盖旧备份：

```bash
uv run --locked python scripts/backup_db.py                          # 备份到 data/backup/
uv run --locked python scripts/backup_db.py --keep 3                 # 备份后只保留最近 3 个
uv run --locked python scripts/backup_db.py --output D:\backup       # 放到别的目录（仍带时间戳）
uv run --locked python scripts/backup_db.py --output D:\backup\a.sqlite3   # 用指定文件名
```

`--output` 给目录就生成带时间戳的文件，并在该目录里执行 `--keep` 轮换；给具体文件名就用那个名字，不参与轮换（免得你的文件被当旧备份删掉）。目标已存在时脚本拒绝覆盖，也拒绝把备份写回数据库本身。

备份完成后，脚本会打开备份文件跑一次 `PRAGMA integrity_check` 并打印各表行数；校验不通过时直接退出，不会删除任何旧备份。

`integrity_check` 查的是**文件结构**：每页归属是否清楚、有没有被两个对象共用、B-tree 的页指针和单元格偏移是否在页内、索引条目是否都指向真实存在的行、空闲页链表是否自洽、记录头与字段类型编码是否合法。它**不查**业务内容——数值对不对、有没有缺行、是否与上游一致，它一概不管；那些要靠抓取时和接口逐字段比对。

`data/` 下三个文件各自的用途：

| 文件 | 作用 | 备份时 |
| --- | --- | --- |
| `screener.sqlite3` | 全部数据 | 必须 |
| `screener.sqlite3-wal` | 尚未合并的写入 | 为 0 字节时不需要；非 0 时必须与主文件一起复制 |
| `screener.sqlite3-shm` | 共享内存索引 | 永远不需要，SQLite 会自动重建 |

## 股票列表（整表替换，剔除 ST）

菜单第 1 项拉两个接口：`stock_basic` 落全量，`stock_st` 落当天 ST 名单，再按代码把 ST 从 `stock_basic` 里删掉（`DELETE ... WHERE ts_code IN (SELECT ts_code FROM stock_st)`）。

为什么拆两张表：`stock_basic` 没有「是不是 ST」的字段（`list_status` 只有 L/D/P），名称又是当前值，只能靠 `stock_st` 按交易日取。实测 20261016 那天 ST 共 201 只，与 `stock_basic` 里名称带 `ST` 的 201 只完全一致；整表替换后 5,577 → **5,376**。

删除只看代码、不看名称，所以名称里带 `ST` 但不是当天 ST 名单的股票不会被误删。`stock_st` 每次整表替换，只保留最近一个交易日。

## 利润表全历史初始化

菜单里的「利润表」只做增量：每次补**最近一个已过法定披露截止日**的报告期（4/30 补一季报和上年年报，8/31 补中报，10/31 补三季报），其余时间点运行不会发起请求。

首次建库、或数据库重建后要灌入全部历史，用 `scripts/income_backfill.py`。它逐股一次拉取全部历史（不做 1 年区间切分），4 并发，约半小时跑完 5,577 只：

```bash
uv run --locked python scripts/income_backfill.py              # 全量
uv run --locked python scripts/income_backfill.py --limit 20   # 只看前 20 只试跑
uv run --locked python scripts/income_backfill.py --restart    # 清空 income 表后重来
```

只存单季口径 `report_type=2`，累计口径不存：策略都按季度计算，两种口径并存会让下游每次先判断用哪一种。

进度记录在 `data/income-backfill-done.txt`，Ctrl+C 中断后重跑会跳过已完成的部分。写入用 upsert（五列主键），重复执行不会产生重复行。**清空过表却留着进度文件，那些股票会被当成已完成而永久缺数据——要重灌必须带 `--restart`。**

## 资产负债表全历史初始化

菜单里的「资产负债表」同样只做增量，规则与利润表一致：每次补最近一个已过法定披露截止日的报告期（4/30 补一季报和上年年报，8/31 补中报，10/31 补三季报），其余时间点运行不会发起请求。

首次建库用 `scripts/balancesheet_backfill.py`。代理单次最多返回 100 行且**静默截断**，所以脚本逐只翻页（`has_more` + `offset`）直到取完，默认 20 并发，实测 4,329 只耗时 55 分钟：

```bash
uv run --locked python scripts/balancesheet_backfill.py              # 全量
uv run --locked python scripts/balancesheet_backfill.py --limit 20   # 只看前 20 只试跑
uv run --locked python scripts/balancesheet_backfill.py --restart    # 清空 balancesheet 表后重来
```

**一个季度只存一行。** 上游对同一报告期会返回多条（修正公告、`update_flag` 0/1 成对行、公告日不变但实际公告日推后的重述，实测平均 1.42 行/期），入库前按 `(f_ann_date, ann_date, update_flag)` 取最新的一条压成一行，主键是 `(ts_code, end_date, report_type)`。被重述覆盖的旧版本不保留。

`report_type` 恒为 `1`（合并报表），上游没有单季或母公司版本。157 个字段全存，列名与 Tushare 同名。

进度记录在 `data/balancesheet-backfill-done.txt`。20 并发会频繁撞代理限频（约 200 次/分钟），脚本对 429 等 30 秒重试；嫌吵就调小 `--workers`。**同样地，清空过表却留着进度文件，要重灌必须带 `--restart`。**

## 财务指标全历史初始化

菜单里的「财务指标」同样只做增量，规则与利润表一致：每次补最近一个已过法定披露截止日的报告期（4/30 补一季报和上年年报，8/31 补中报，10/31 补三季报），其余时间点运行不会发起请求。

首次建库用 `scripts/fina_indicator_backfill.py`。同样是 100 行上限 + `offset` 翻页，但多数股票历史不足 100 期，一只只要 1 次调用，默认 20 并发，实测 5,577 只耗时 2 小时、写入 325,367 行：

```bash
uv run --locked python scripts/fina_indicator_backfill.py              # 全量
uv run --locked python scripts/fina_indicator_backfill.py --limit 20   # 只看前 20 只试跑
uv run --locked python scripts/fina_indicator_backfill.py --restart    # 清空 fina_indicator 表后重来
```

**一个季度只存一行。** 该接口没有 `report_type`，但同一报告期会返回修订版本（实测 2024 年报全市场 6,584 只里有 944 组多行，全部是 `update_flag` 0/1 成对），入库前按 `(ann_date, update_flag)` 取最新一条，主键是 `(ts_code, end_date)`。

**`fields` 必须显式列全 109 列**：逐股路径不传 `fields` 时只返回 108 列、**缺 `update_flag`**，省掉就等于丢掉版本信息。`start_date`/`end_date` 在该接口是**报告期**语义，与利润表、资产负债表的公告日语义不同。

进度记录在 `data/fina-indicator-backfill-done.txt`。**同样地，清空过表却留着进度文件，要重灌必须带 `--restart`。**

## 分红送配全历史初始化

菜单里的「分红送配」只做增量，水位是 `MAX(record_date)`（股权登记日，且按今天截断——回填会带进已公告但未到登记日的方案，不截断会跳过中间的日子），每次把水位之后的每个交易日各取一次，其余时间点运行不会发起请求。表是空的时候不发请求——历史交给下面的回填脚本，菜单不猜起点。

首次建库用 `scripts/dividend_backfill.py`。逐股一次调用就拿到全部历史，**不用翻页**（实测最长的股票也只有 98 行，远低于 2,000 行的每页上限），默认 20 并发：

```bash
uv run --locked python scripts/dividend_backfill.py              # 全量
uv run --locked python scripts/dividend_backfill.py --limit 20   # 只看前 20 只试跑
uv run --locked python scripts/dividend_backfill.py --restart    # 清空 dividend 表后重来
```

**只存已经实施的方案**（`div_proc='实施'`），未实施的预案不入库。上游对同一笔分红会按进度追加行（预案 → 股东大会通过 → 实施，实测还有预披露、股东提议、股东大会未通过、未通过、停止实施），抽样里实施行只占 23%，其余对第 1 集没有用。

**一个方案一行**，主键 `(ts_code, end_date)`。同一报告期的多条实施行按 `(imp_ann_date, ann_date)` 取最新一条（茅台 2024 年报原始 5 行，压成 1 行）。**同一个报告期不会出现两笔金额不同的实施分红**（抽样 100 个方案里 0 个），所以两列主键够。

**同一笔被挂到两个报告期**时要再收一次：宁德时代 2024 中期那笔 1.23 元同时出现在 `20240930` 和 `20241211` 下，`ex_date` 都是 `20250124`，两列主键压不掉。按 `(ts_code, ex_date, cash_div_tax)` 保留报告期较早的那条。

**参数上踩过的坑**（都是实测）：

- `start_date` 不支持。单独传报 `50101 参数校验失败`，带 `ts_code` 时被无视（返回的行和不传一样），跟 `end_date` 一起传则静默返回 0 行。
- `record_date` 是**精确匹配的单日**（`record_date=20250626` → 137 行），所以「`record_date > MAX`」只能靠 `trade_cal` 逐个交易日枚举。
- `end_date=` 只在按报告期取全市场时有用，**每页硬顶 2,000 行**（比资产负债表的 100 行高），`offset` 翻页；实测 2024 年报 15,111 行 / 8 次调用 / 49 秒。
- 增量这条路走**直连代理**：生产 SDK 代理不接受 `dividend` 的 `end_date` 单独过滤（`400 invalid_params`），只认 `ts_code` 这类官方参数。

进度记录在 `data/dividend-backfill-done.txt`。**同样地，清空过表却留着进度文件，要重灌必须带 `--restart`。**

## 第 1 集筛选（格雷厄姆）

菜单「2. 运行选股」→「1. 格雷厄姆（可用）」，输入日期后按 `strategies.md` 的 E01-1～E01-8 逐项判定，结果写到 `outputs/episode-01-graham/screen-<时间戳>.html`。留空＝上一交易日。代码在 `screener/strategies/episode_01_graham.py`，逐项口径见该文件的模块说明。

**报告期取「披露完一轮」的那一期，不取 `MAX(end_date)`。** 报告季刚开头时，`income` 已经有几百家先披露的当期数据，直接取最新会把绝大多数股票判成缺数据（2026-10-16 实测：20260930 只有 111 家，能用的应该是 20260630）。实现从新到旧找第一个覆盖率不低于上一期 90% 的报告期；资产负债表用同一期。这里用相对覆盖率而不是「占股票总数之比」，是因为历史日期的股票池本来就更小。

**单位**是这一步最容易出错的地方，都已核对：`income`/`balancesheet` 的金额是**元**，`daily_basic.total_mv` 是**万元**，`dividend.base_share` 是**万股**，`fina_indicator.roe_waa` 已经是**百分数**（19.32 即 19.32%）。所以 `cash_div_tax × base_share` 直接得万元分红额，与 `total_mv` 相除不用换算；市盈率、市净率要先把手里的元换算成万元。

**市净率、市盈率都按本集定义自己算**，不用 `daily_basic.pb`（官方定义是「净资产－其他权益工具」）和 `daily_basic.pe_ttm`（不含后来才公布的财报，做 12 个季度样本时口径会漂）。

**长期负债**本地没有 `total_ncl` 列，用 `total_liab - total_cur_liab` 得到（2026-10-22 拿 9 个报告期与接口值核对，差 0.00）。分母为 0 时按 `strategies.md` 的口径：分子 > 0 视为满足，分子 ≤ 0 不满足；分母 < 0 直接排除。

**回填脚本收尾会补记一次成功批次**（`store.publish_backfill`）。之前只有菜单更新才留批次，用脚本灌出来的库会让筛选一直报「本地数据不完整」——脚本直接写表，菜单的「就绪」判定看不见。已经灌好的库不必重跑脚本，脚本会按表里的实际行数登记。

2026-10-16 的实测结果：股票池 4,975 只 → 有当期行情和 4 个单季 4,929 → 有资产负债表 4,879 → 3 年年报 ROE 4,797 → 12 个样本齐全 4,560 → 格雷厄姆数 < 22.5 有 1,747 → 平均市盈率 < 15 有 1,159 → 3 年 ROE 与净利润为正 288 → 流动比率 > 2 有 75 → 市值 > 50 亿 53 → 全部通过 **32 只**（江苏国泰、三角轮胎、海螺水泥、大秦铁路……），整轮约 8 秒。

## 项目约束

筛选或重算只读取本地已保存数据；数据更新另行执行。所有策略共用的日期行为和结果交付见 [产品需求](product.md)。

## 后续工作

1. 第 1 集已实现，等用户人工验收。开发 AI 按数据源定义处理字段和单位；遇到确实无法映射业务口径的数据案例，再记录样例和影响。改指标后的重算读取本地数据，不把远程拉取放进一次计算。
2. 逐集核对第 2～7 集，并为尚未确定的指标写出各自的实现口径。
