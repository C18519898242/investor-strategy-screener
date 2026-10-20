"""备份本地数据库。

用 SQLite 的 `VACUUM INTO` 生成一致性快照，产出一个独立的单文件备份。
WAL 模式下直接复制 `data/` 里的文件，在库被占用时可能拿到主文件与 `-wal`
不是同一时刻的组合：轻则丢最近的写入，重则备份打不开。

用法（在任意目录执行都可以）：

    uv run --locked python scripts/backup_db.py
    uv run --locked python scripts/backup_db.py --keep 3
    uv run --locked python scripts/backup_db.py --output D:\\backup
    uv run --locked python scripts/backup_db.py --output D:\\backup\\today.sqlite3
"""

import argparse
import sqlite3
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "screener.sqlite3"
BACKUP_DIR = ROOT / "data" / "backup"
TABLES = ("income", "daily_basic", "stock_basic", "trade_cal", "suspend_d")
NAME_PATTERN = "screener-*.sqlite3"


def show(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def resolve_target(output: str | None) -> tuple[Path, bool]:
    """返回（目标文件，能不能在它所在的目录里按 --keep 轮换）。

    给了目录就放一个带时间戳的文件进去，可以轮换；给了具体文件名就用那个名字，
    不参与轮换——那个文件是你的，不该被别人删掉。
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if output is None:
        return BACKUP_DIR / f"screener-{stamp}.sqlite3", True

    given = Path(output).expanduser()
    if not given.is_absolute():
        given = Path.cwd() / given
    if given.is_dir() or str(output).endswith(("/", "\\")):
        return given / f"screener-{stamp}.sqlite3", True
    return given, False


def main() -> None:
    parser = argparse.ArgumentParser(description="备份本地数据库，并校验备份文件。")
    parser.add_argument("--keep", type=int, default=0, help="只保留最近 N 个备份，0 表示全部保留")
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="备份放哪：给目录就在里面生成带时间戳的文件，给文件名就用那个名字。默认 data/backup",
    )
    args = parser.parse_args()

    if not SOURCE.exists():
        raise SystemExit(f"找不到数据库：{show(SOURCE)}")

    for suffix in ("-wal", "-shm"):
        side = Path(str(SOURCE) + suffix)
        size = side.stat().st_size if side.exists() else 0
        print(f"  {suffix}: {size:,} 字节")

    target, rotate = resolve_target(args.output)
    target = target.resolve()
    if target == SOURCE.resolve():
        raise SystemExit("备份目标就是数据库本身，换个路径。")
    if target.exists():
        raise SystemExit(f"目标已存在，不覆盖：{show(target)}")
    target.parent.mkdir(parents=True, exist_ok=True)

    print(f"\n备份 {SOURCE.stat().st_size / 1024**3:.2f} GB → {show(target)}")
    started = time.perf_counter()
    escaped = target.as_posix().replace("'", "''")
    source = sqlite3.connect(SOURCE)
    try:
        source.execute(f"VACUUM INTO '{escaped}'")
    finally:
        source.close()
    elapsed = time.perf_counter() - started
    print(f"完成，耗时 {elapsed:.1f} 秒，备份文件 {target.stat().st_size / 1024**3:.2f} GB")

    print("\n校验备份文件")
    backup = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    try:
        integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
        print(f"  integrity_check  {integrity}")
        for table in TABLES:
            try:
                count = backup.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"  {table:<14}{count:>12,}")
            except sqlite3.OperationalError:
                print(f"  {table:<14}{'表不存在':>12}")
    finally:
        backup.close()

    if integrity != "ok":
        raise SystemExit("备份文件校验失败，请勿删除旧备份。")

    if args.keep <= 0:
        return
    if not rotate:
        print("  指定了具体文件名，不做 --keep 轮换")
        return
    backups = sorted(target.parent.glob(NAME_PATTERN))
    for old in backups[: -args.keep]:
        old.unlink()
        print(f"  删除旧备份 {show(old)}")


if __name__ == "__main__":
    main()
