import logging

from screener.config import Settings
from screener.sync.errors import SyncError

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 120


def read_tushare_config(settings: Settings) -> tuple[str, str]:
    """读取必填的 Tushare 配置；缺失项一次性全部报出。"""
    token = settings.tushare_token.strip()
    api_url = settings.tushare_api_url.strip()
    missing = [
        name
        for name, value in (("TUSHARE_TOKEN", token), ("TUSHARE_API_URL", api_url))
        if not value
    ]
    if missing:
        raise SyncError(f"缺少 {'、'.join(missing)}，请在 .env 或环境变量中设置")
    return token, api_url


def create_tushare_client(settings: Settings) -> object:
    import tushare as ts

    token, api_url = read_tushare_config(settings)
    client = ts.pro_api(token, timeout=TIMEOUT_SECONDS)
    client._DataApi__http_url = api_url
    logger.debug("客户端就绪：timeout=%d 秒，地址 %s", TIMEOUT_SECONDS, api_url)
    return client
