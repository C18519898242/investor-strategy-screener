from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    """运行配置。字段名小写，环境变量名为同名大写（TUSHARE_TOKEN）。"""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    tushare_token: str = ""
    tushare_api_url: str = ""


def load_settings(env_file: Path | None = None) -> Settings:
    """读取配置：真实环境变量优先于 .env；.env 不存在时使用默认值。"""
    path = ENV_FILE if env_file is None else env_file
    return Settings(_env_file=path)
