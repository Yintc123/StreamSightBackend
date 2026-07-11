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

    # email 加密存 DB (AES-256-CBC + fixed IV, deterministic → unique/index 可用)
    email: Mapped[str] = mapped_column(
        # length 改成 1024，由於 eamil 會加密完存入 DB，故預留較大的空間
        DeterministicEncryptedString(key=_ENCRYPTION_KEY, length=1024),
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(default=True)

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"
