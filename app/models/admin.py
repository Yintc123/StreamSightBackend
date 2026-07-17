"""Admin model — CMS 後台管理者（獨立於一般 User 的高權限身分）。

設計理由見 docs/decisions/jwt-role-and-admin.md D4/D5：
    - 與一般 User 不同生命週期／關注點，故各自成表，共通身分由 principals 抽象。
    - Admin 自帶 password_hash（argon2id），不沿用 Identity 多身分（CMS 只需密碼登入）。
    - 常數判別欄 role=1 + 複合 FK + CHECK(role=1) 硬化型別-角色一致性（見 D9）。
"""

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_app_settings
from app.core.db import Base
from app.core.db.types import DeterministicEncryptedString

# 比照 app/models/user.py 於 module import 時綁定加密金鑰（Option A）
_ENCRYPTION_KEY: bytes = get_app_settings().encryption_key.get_secret_value().encode()


class Admin(Base):
    __tablename__ = "admins"

    # 一對一掛上 principals（複合 FK 承擔參照）
    principal_id: Mapped[int] = mapped_column(unique=True, index=True)
    # 常數判別欄：Admin 永遠 role=1，被複合 FK + CHECK(role=1) 釘死（見 D9）
    role: Mapped[int] = mapped_column(SmallInteger, default=1, server_default=text("1"))

    # 加密存 DB（deterministic → unique/index 可用）；admin email 各自獨立命名空間
    email: Mapped[str] = mapped_column(
        DeterministicEncryptedString(key=_ENCRYPTION_KEY, length=1024),
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))  # argon2id
    # is_active 留在 child 本地欄位（見 D4b）
    is_active: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["principal_id", "role"],
            ["principals.id", "principals.role"],
            ondelete="CASCADE",
            name="fk_admins_principal_role",
        ),
        CheckConstraint("role = 1", name="ck_admins_role_admin"),
    )

    def __repr__(self) -> str:
        return f"<Admin id={self.id} email={self.email!r}>"
