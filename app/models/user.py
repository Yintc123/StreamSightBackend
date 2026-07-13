from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_app_settings
from app.core.db import Base
from app.core.db.types import DeterministicEncryptedString

# Load encryption key at module import time (Option A: init-time binding).
# Column type instances are class-level, so the key is bound once per process.
_ENCRYPTION_KEY: bytes = get_app_settings().encryption_key.get_secret_value().encode()


class User(Base):
    __tablename__ = "users"

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
    is_active: Mapped[bool] = mapped_column(default=True)

    # 認證 credential (password hash、OAuth sub) 存在 Identity 表、不在此
    # 見 app/models/identity.py

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"
