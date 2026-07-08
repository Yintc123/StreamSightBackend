import os
from functools import lru_cache

from .base import BaseAppSettings
from .local import LocalAppSettings
from .dev import DevAppSettings
from .prod import ProdAppSettings
from .test import TestAppSettings

from app.core.enums import AppEnv

_ENV_MAP: dict[AppEnv, type[BaseAppSettings]] = {
    AppEnv.LOCAL: LocalAppSettings,
    AppEnv.DEVELOPMENT: DevAppSettings,
    AppEnv.PRODUCTION: ProdAppSettings,
    AppEnv.TEST: TestAppSettings,
}

# 用 lru_cache 快取 get_app_settings 的值，
# 避免每次讀取環境變數重新讀取 .env 檔
@lru_cache
def get_app_settings() -> BaseAppSettings:
    env_str: str = os.getenv("APP_ENV", AppEnv.LOCAL.value).lower()
    try:
        valid_env: AppEnv = AppEnv(env_str)
    except ValueError as e:
        raise ValueError(
            f"Unknown APP_ENV = {env_str}. valid: {[env.value for env in AppEnv]}"
        ) from e

    settings_cls: type[BaseAppSettings] = _ENV_MAP.get(valid_env, LocalAppSettings)
    return settings_cls()

# 對外只 export 這兩個物件，使用者不需要知道不同環境是否有分檔
__all__ = ["BaseAppSettings", "get_app_settings"]