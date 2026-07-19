"""Admin model — CMS 後台管理者（獨立於一般 User 的高權限身分）。

設計理由見 docs/decisions/jwt-role-and-admin.md D4/D5 與
docs/specs/admin-account-refinement.md：
    - 與一般 User 不同生命週期／關注點，故各自成表，共通身分由 principals 抽象。
    - Admin 自帶 password_hash（argon2id），不沿用 Identity 多身分（CMS 只需密碼登入）。
    - 常數判別欄 role=1 + 複合 FK + CHECK(role=1) 硬化型別-角色一致性（見 D9）。
    - 登入識別改用 username（非加密、正規化）；封存/軟刪除以時間戳表達。
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.enums import AdminRole


class Admin(Base):
    __tablename__ = "admins"

    # 一對一掛上 principals（複合 FK 承擔參照）——不變
    principal_id: Mapped[int] = mapped_column(unique=True, index=True)
    # 常數判別欄：Admin 永遠 role=1，被複合 FK + CHECK(role=1) 釘死（見 D9）。
    # ⚠️ 這是「型別判別子」（帳號是 admin），與下方 admin_role（權限等級）完全不同，勿混淆。
    role: Mapped[int] = mapped_column(SmallInteger, default=1, server_default=text("1"))

    # 登入識別：非加密、唯一索引；service 層正規化為小寫後儲存（見 §5.3）
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))  # argon2id

    # 權限等級（可變，供 rbac 授權）。⚠️ 與上方常數 role 判別子不同層次（見 §2.7）。
    # 存 int（rank = value，IntEnum）+ CHECK 硬化；預設 VIEWER（最低權限 fail-safe）。
    # 見 docs/specs/enum-int.md：SmallInteger、`admin_role IN (0,50,100,999)`、上限 < 1000。
    admin_role: Mapped[int] = mapped_column(
        SmallInteger,
        default=AdminRole.VIEWER.value,
        server_default=text("0"),
    )

    # 受保護 root 標記（seed-only、不可經 API 切換）：把「≥1 super_admin」降為單列不變式。
    # 受保護者恆為 active super_admin（由下方兩條 CHECK ＋ service 守衛保證）。
    # 見 docs/specs/admin-management-model.md §2.1/§2.3。
    is_protected: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))

    # 封存 / 軟刪除時間戳（NULL = 未發生）＋ 操作者稽核（誰做的）
    # 成對不變式：archived_at 與 archived_by 同進同退（deleted_* 同理），見 §2.2。
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    archived_by: Mapped[int | None] = mapped_column(
        ForeignKey("principals.id", ondelete="SET NULL"), nullable=True, default=None
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    deleted_by: Mapped[int | None] = mapped_column(
        ForeignKey("principals.id", ondelete="SET NULL"), nullable=True, default=None
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["principal_id", "role"],
            ["principals.id", "principals.role"],
            ondelete="CASCADE",
            name="fk_admins_principal_role",
        ),
        CheckConstraint("role = 1", name="ck_admins_role_admin"),
        CheckConstraint(
            "admin_role IN (0, 50, 100, 999)",
            name="ck_admins_admin_role",
        ),
        # 受保護者必為 ROOT（999，bootstrap root）；間接硬化「受保護者不可降級」。§2.6
        CheckConstraint(
            "is_protected = 0 OR admin_role = 999",
            name="ck_admins_protected_is_super",
        ),
        # 受保護者恆為 active（未封存、未軟刪除）——使「root 恆 active super_admin」除
        # bootstrap 外全由 DB 保證。§2.3
        CheckConstraint(
            "is_protected = 0 OR (archived_at IS NULL AND deleted_at IS NULL)",
            name="ck_admins_protected_is_active",
        ),
    )

    @property
    def is_active(self) -> bool:
        """封存或軟刪除皆視為不可用（登入／refresh／授權共用；見 §2.3）。"""
        return self.archived_at is None and self.deleted_at is None

    def __repr__(self) -> str:
        return f"<Admin id={self.id} username={self.username!r}>"
