import os
from functools import lru_cache

from .base import BaseAppSettings
from .local import LocalAppSettings
from .dev import DevAppSettings
from .prod import ProdAppSettings

_ENV_MAP: dict[str, type[BaseAppSettings]] = {
    "local": LocalAppSettings,
    "development": DevAppSettings,
    "production": ProdAppSettings
}

# 用 lru_cache 快取 get_app_settings 的值，
# 避免每次讀取環境變數重新讀取 .env 檔
@lru_cache
def get_app_settings() -> BaseAppSettings:
    env: str = os.getenv("APP_ENV", "local").lower()
    settings_cls: type[BaseAppSettings] = _ENV_MAP.get(env, LocalAppSettings)

    return settings_cls()

# 對外只 export 這兩個物件，使用者不需要知道不同環境是否有分檔
__all__ = ["BaseAppSettings", "get_app_settings"]