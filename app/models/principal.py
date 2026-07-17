"""Principal supertype — User 與 Admin 的共同父表（帳號共通身分）。

設計理由見 docs/decisions/jwt-role-and-admin.md D1：
    - `users` / `admins` 各以 principal_id（unique FK → principals，CASCADE）一對一掛上。
    - `role` 是型別判別子（0=User, 1=Admin），存於父表；建立後不可變。
    - 任何「屬於某帳號」的資料（refresh_tokens）FK → principals.id，統一擁有者、
      完整性由 DB 保證（刪 principal 連帶清 child + refresh_tokens）。

父表只承載判別子 `role`；帳號狀態（is_active）與識別屬性（email/name）留在各 child
（見 D4b：async 下高頻已知型別讀取免 join、結構上免疫 MissingGreenlet）。
"""

from sqlalchemy import CheckConstraint, SmallInteger, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Principal(Base):
    __tablename__ = "principals"

    # role 判別子（0=User, 1=Admin），帳號建立後不可變。
    # 不加獨立 index：role 低選擇性、無「用 role 查 principals」的熱路徑
    # （refresh 走 PK lookup；要列 admin 直接查 admins 表）。
    role: Mapped[int] = mapped_column(SmallInteger)

    __table_args__ = (
        # 供 child 的複合 FK 參照（型別-角色一致性硬化，見 D9）；id 已是 PK，此約束純為 FK 目標
        UniqueConstraint("id", "role", name="uq_principals_id_role"),
        # 父表 role 值域硬化：DB 層擋掉無對應 child 型別的 role（如 role=5），對齊 integrity-first
        CheckConstraint("role IN (0, 1)", name="ck_principals_role_domain"),
    )

    def __repr__(self) -> str:
        return f"<Principal id={self.id} role={self.role}>"
