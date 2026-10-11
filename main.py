import os
from pathlib import Path

from screener.cli import main as run_menu
from screener.store import open_database


def main() -> int:
    connection = open_database(Path("data") / "screener.sqlite3")
    return run_menu(connection=connection, env=os.environ)


if __name__ == "__main__":
    raise SystemExit(main())
