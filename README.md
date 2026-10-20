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
| 第 1 集 | 格雷厄姆 | 已核对视频画面与口播；[Q01～Q12 的业务口径](sources/episode-01-graham/open-questions.md)已确定，可以进入开发。逐项筛选，任一条件不满足或数据不足就排除该股票；最终生成带 CSS、只含入选股票的 HTML 列表。历史日期按本次运行的本地数据重算。 |
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

## 项目约束

筛选或重算只读取本地已保存数据；数据更新另行执行。所有策略共用的日期行为和结果交付见 [产品需求](product.md)。

## 后续工作

1. 编写第 1 集选股代码，并交用户人工验收。开发 AI 按数据源定义处理字段和单位；遇到确实无法映射业务口径的数据案例，再记录样例和影响。实现须满足上面的项目约束：改指标后的重算读取本地数据，不把远程拉取放进一次计算。
2. 逐集核对第 2～7 集，并为尚未确定的指标写出各自的实现口径。
