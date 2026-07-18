"""Initial super admin — 憑證存 config／SSM、**不進 DB** 的特例帳號。

設計:憑證(username + argon2 雜湊)由 config 提供(SSM 注入),非 admins 表的一列。
以哨兵 principal_id 0（真實 principals PK 從 1 起跳,永不衝突）代表這個帳號:
- 登入只發 access token（無 DB principal 可掛 refresh）。
- get_current_admin 見 sub=0 → 合成一個記憶體 Admin(super_admin)、不查 DB。
- 因不在 DB:不出現在列表、無 id、不可被封存／刪除／改名／改密碼／鎖死。

用途:取代舊 seed 腳本——**第一位 super admin 就是這個 SSM 帳號**;登入後即可建立
DB admin。它恆可登入,故也天然保證「系統永遠有一位 super admin 可用」。
env:INITIAL_ADMIN_USERNAME + INITIAL_ADMIN_PASSWORD_HASH(兩者皆非空才啟用)。
"""

from app.core.config import get_app_settings
from app.core.enums import AdminRole
from app.core.security import normalize_username
from app.models.admin import Admin

# 哨兵 principal_id / id。principals 自增 PK 從 1 起,0 永不與真實帳號衝突。
INITIAL_ADMIN_PRINCIPAL_ID: int = 0


def initial_admin_enabled() -> bool:
    """兩個 config 皆非空才啟用（避免半設定狀態誤開）。"""
    s = get_app_settings()
    return bool(s.initial_admin_username and s.initial_admin_password_hash.get_secret_value())


def _normalized_username() -> str:
    """正規化後的 username（與登入輸入的正規化一致比對）。"""
    return normalize_username(get_app_settings().initial_admin_username)


def is_initial_admin_username(username: str) -> bool:
    """已啟用且 username（正規化）命中初始 admin。"""
    return initial_admin_enabled() and username == _normalized_username()


def initial_admin_hash() -> str:
    """設定的 argon2id 雜湊（供登入 verify）。"""
    return get_app_settings().initial_admin_password_hash.get_secret_value()


def build_initial_admin() -> Admin:
    """合成一個**未附著 session** 的 Admin,代表初始 super admin（super_admin、active）。

    僅供 get_current_admin 回傳給下游讀屬性(admin_role/principal_id/id/is_active/username);
    絕不 add 進 session、不落地 DB。
    """
    username = _normalized_username()
    admin = Admin(
        username=username,
        name=get_app_settings().initial_admin_name or username,  # 可選顯示名，空 → 用 username
        password_hash=initial_admin_hash(),
        admin_role=AdminRole.SUPER_ADMIN.value,
        is_protected=True,
        principal_id=INITIAL_ADMIN_PRINCIPAL_ID,
    )
    admin.id = INITIAL_ADMIN_PRINCIPAL_ID
    return admin
