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

# Load encryption key at module import time (Option A: init-time binding).
# Column type instances are class-level, so the key is bound once per process.
_ENCRYPTION_KEY: bytes = get_app_settings().encryption_key.get_secret_value().encode()


class User(Base):
    __tablename__ = "users"

    # 一對一掛上 principals 父表（複合 FK 承擔參照，不再 inline ForeignKey）。
    # 見 docs/decisions/jwt-role-and-admin.md D1。
    principal_id: Mapped[int] = mapped_column(unique=True, index=True)
    # 常數判別欄：User 永遠 role=0，被複合 FK + CHECK(role=0) 釘死，不能被改成 1（見 D9）。
    role: Mapped[int] = mapped_column(SmallInteger, default=0, server_default=text("0"))

    # email 可能沒有:
    #   - 密碼登入用戶:一定有(email 是登入識別)
    #   - OAuth 用戶:可能沒(某些 provider 隱藏 email、例如 Apple sign in)
    # 有 email 時強制 unique(防重複註冊)、nullable 允許 OAuth 無 email 情境
    #
    # 加密存 DB (AES-256-CBC + fixed IV, deterministic → unique/index 可用)
    # length 1024:email 明文最長 254 bytes、hex 後最長 512 chars、留 buffer
    email: Mapped[str | None] = mapped_column(
        DeterministicEncryptedString(key=_ENCRYPTION_KEY, length=1024),
        unique=True,
        index=True,
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(100))
    # is_active 留在 child（本地欄位）：高頻已知型別讀取免 join、async 免 MissingGreenlet（見 D4b）
    is_active: Mapped[bool] = mapped_column(default=True)

    # 認證 credential (password hash、OAuth sub) 存在 Identity 表、不在此
    # 見 app/models/identity.py

    __table_args__ = (
        # 型別-角色一致性硬化：(principal_id, role) → principals(id, role)，錯配即 IntegrityError
        ForeignKeyConstraint(
            ["principal_id", "role"],
            ["principals.id", "principals.role"],
            ondelete="CASCADE",
            name="fk_users_principal_role",
        ),
        CheckConstraint("role = 0", name="ck_users_role_user"),
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"
