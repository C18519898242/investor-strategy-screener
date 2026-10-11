from collections.abc import Callable, Mapping

from screener.store import begin_sync


class SyncError(Exception):
    pass


def read_token(env: Mapping[str, str]) -> str:
    token = env.get("TUSHARE_TOKEN")
    if not token:
        raise SyncError("缺少环境变量 TUSHARE_TOKEN")
    return token


def fetch_not_implemented(datasets: tuple[str, ...], token: str) -> Mapping[str, int]:
    raise NotImplementedError("Tushare 同步尚未实现")


def sync_all(
    connection,
    *,
    datasets: tuple[str, ...],
    env: Mapping[str, str],
    fetch: Callable[[tuple[str, ...], str], Mapping[str, int]],
    finished_at: str,
) -> str:
    token = read_token(env)
    batch = begin_sync(connection)
    try:
        counts = fetch(datasets, token)
        for dataset in datasets:
            batch.record(dataset, counts[dataset])
        batch.publish(finished_at)
    except Exception:
        batch.abort()
        raise
    return finished_at
