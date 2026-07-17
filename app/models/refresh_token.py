"""RefreshToken model — opaque refresh token 的伺服器端狀態（DB 為真實來源）。

設計理由見 `docs/decisions/refresh-token-rotation.md`：
    - refresh token 是 opaque 隨機字串，DB 只存其 HMAC-SHA256 hash（不存明文）。
    - rotation：每次 refresh 撤銷舊 token、發新 token，同一登入 session 共用 family_id。
    - reuse detection：已撤銷的 token 再現 → 撤銷整條 family。
    - replaced_by_id 串起輪替鏈（audit）。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    # 綁定到哪個 user（user 刪除時一併清除）
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    # HMAC-SHA256(pepper, plaintext) 的 hex digest；查詢與唯一性都靠它
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # 同一次登入 session 的輪替鏈共用同一 family_id（reuse detection 用）
    family_id: Mapped[str] = mapped_column(String(36), index=True)  # str(uuid4())
    # 過期時間（絕對時間戳）
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 撤銷時間；NULL = 仍有效。rotation / logout / reuse 撤銷都寫這裡
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 輪替鏈：本 token 被哪一筆新 token 取代（audit / debug 用），發放時為 NULL
    replaced_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<RefreshToken id={self.id} user_id={self.user_id} family={self.family_id!r}>"
