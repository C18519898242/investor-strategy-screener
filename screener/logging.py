import logging
from collections.abc import Callable
from pathlib import Path

LOGGER_NAME = "screener"
FILE_LEVEL = logging.DEBUG
CONSOLE_LEVEL = logging.INFO
FILE_FORMAT = "%(asctime)s %(levelname)-5s %(name)s  %(message)s"
FILE_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_installed: list[logging.Handler] = []


class ConsoleHandler(logging.Handler):
    """把日志正文交给注入的输出函数；异常堆栈只留给文件。"""

    def __init__(self, console: Callable[[str], None]):
        super().__init__(level=CONSOLE_LEVEL)
        self._console = console

    def emit(self, record: logging.LogRecord) -> None:
        record = logging.makeLogRecord(record.__dict__)
        record.exc_info = None
        record.exc_text = None
        self._console(self.format(record))


def setup_logging(*, log_file: Path, console: Callable[[str], None]) -> None:
    """装上文件与控制台两个输出源；重复调用不会累积 handler。"""
    shutdown_logging()

    log_file.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(FILE_LEVEL)
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT, FILE_DATE_FORMAT))

    console_handler = ConsoleHandler(console)
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(FILE_LEVEL)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    _installed.extend((file_handler, console_handler))


def shutdown_logging() -> None:
    """移除并关闭自己装的 handler，避免测试之间互相污染。"""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in _installed:
        logger.removeHandler(handler)
        handler.close()
    _installed.clear()
