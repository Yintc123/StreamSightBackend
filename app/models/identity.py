"""Identity model — 用戶的認證方式(可多個 provider 綁定同一 user)。

設計理由:一 User 可能有多個登入方式(密碼 + Google + GitHub)。
把 credential 從 users 表拆到 identities 表:
    - users:identity-agnostic、只放使用者資料
    - identities:每個 (user, provider) 一筆 row、存 credential 或 provider sub

範例:
    Alice 註冊時用 email + password → 建 User + Identity(provider="password")
    Alice 之後綁定 Google → 新增 Identity(provider="google", provider_user_id="sub_from_google")
    Alice 有 2 個 identity、可任一登入
"""

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Identity(Base):
    __tablename__ = "identities"

    # 綁定到哪個 user
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )

    # provider 種類:
    #   "password"  — 密碼登入 (credential = argon2 hash、provider_user_id = NULL)
    #   "google"    — Google OAuth  (credential = ""、provider_user_id = Google 的 sub)
    #   "github"    — GitHub OAuth (credential = ""、provider_user_id = GitHub 的 id)
    #   "apple"     — Apple sign in
    #   ...
    provider: Mapped[str] = mapped_column(String(32), index=True)

    # OAuth provider 的用戶識別碼 (通常是 sub claim)、密碼登入不用
    provider_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # 認證憑證:
    #   - 密碼登入 → argon2id hash
    #   - OAuth → 空字串 (驗證靠 provider_user_id 對照 OAuth token 的 sub)
    credential: Mapped[str] = mapped_column(String(255), default="", server_default="")

    __table_args__ = (
        # 一個 user 一種 provider 只能綁一個
        # (例如 Alice 不能有兩個 password identity、也不能有兩個 google identity)
        UniqueConstraint("user_id", "provider", name="uq_identity_user_provider"),
        # 同一個 OAuth account 只能綁一個 user
        # (例如同一個 Google sub 不能同時對到兩個 User)
        UniqueConstraint("provider", "provider_user_id", name="uq_identity_provider_sub"),
    )

    def __repr__(self) -> str:
        return f"<Identity user_id={self.user_id} provider={self.provider!r}>"
