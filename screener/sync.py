from collections.abc import Callable, Mapping

from screener.config import Settings
from screener.store import begin_sync


class SyncError(Exception):
    pass


def create_tushare_client(settings: Settings) -> object:
    import tushare as ts

    token, api_url = read_tushare_config(settings)
    client = ts.pro_api(token)
    client._DataApi__http_url = api_url
    return client


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


def fetch_not_implemented(datasets: tuple[str, ...], token: str) -> Mapping[str, int]:
    raise NotImplementedError("Tushare 同步尚未实现")


def sync_all(
    connection,
    *,
    datasets: tuple[str, ...],
    settings: Settings,
    fetch: Callable[[tuple[str, ...], str], Mapping[str, int]],
    finished_at: str,
) -> str:
    token, _ = read_tushare_config(settings)
    batch = begin_sync(connection)
    try:
        counts = fetch(datasets, token)
        missing = [dataset for dataset in datasets if dataset not in counts]
        if missing:
            raise RuntimeError("同步结果缺少所需数据")
        for dataset in datasets:
            batch.record(dataset, counts[dataset])
        batch.publish(finished_at)
    except Exception:
        batch.abort()
        raise
    return finished_at
