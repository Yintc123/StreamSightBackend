"""seed_300_records — 種入 300 筆假資料給 records 表（開發 / 展示用）

Revision ID: d0e1f2a3b4c5
Revises: e1f2a3b4c5d6
Create Date: 2026-07-19

每分類 75 筆 × 4 分類（感測器 / 系統 / 應用 / 網路）= 300 筆，
分散於 2026-01-20 ～ 2026-07-19（180 天），以 Random(42) 確保重複執行結果相同。

created_by_principal_id：從 e1f2a3b4c5d6 seed 的 10 位 admin（seed_admin_01～10）
以 Random(42) 隨機分配——前提是 e1f2a3b4c5d6 已先執行。

downgrade ⚠️ 會清空整個 records 表（包含人工建立的資料），僅供開發 / 測試環境。
"""

import random
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ── 每分類的標題範本與數值區間 ───────────────────────────────────────────
# (category_id, titles, value_min, value_max)
_CATEGORY_CFG: list[tuple[int, list[str], float, float]] = [
    (
        1,  # 感測器
        [
            "溫度感測器 #{}",
            "濕度感測器 #{}",
            "氣壓感測器 #{}",
            "光照感測器 #{}",
            "CO₂ 感測器 #{}",
            "振動感測器 #{}",
            "電流感測器 #{}",
            "電壓感測器 #{}",
            "流量感測器 #{}",
            "液位感測器 #{}",
        ],
        0.0,
        100.0,
    ),
    (
        2,  # 系統
        [
            "CPU 使用率 #{}",
            "記憶體用量 #{}",
            "磁碟 I/O #{}",
            "核心溫度 #{}",
            "系統負載 #{}",
            "程序數量 #{}",
            "檔案描述符 #{}",
            "上線時間 #{}",
            "Context Switch #{}",
            "中斷次數 #{}",
        ],
        0.0,
        100.0,
    ),
    (
        3,  # 應用
        [
            "API 請求數 #{}",
            "平均延遲 #{}",
            "錯誤率 #{}",
            "Session 數量 #{}",
            "快取命中率 #{}",
            "佇列深度 #{}",
            "訂閱人數 #{}",
            "訊息吞吐量 #{}",
            "WebSocket 連線數 #{}",
            "背景任務數 #{}",
        ],
        0.0,
        5000.0,
    ),
    (
        4,  # 網路
        [
            "入站頻寬 #{}",
            "出站頻寬 #{}",
            "封包遺失率 #{}",
            "RTT #{}",
            "TCP 連線數 #{}",
            "UDP 封包數 #{}",
            "DNS 查詢數 #{}",
            "防火牆阻擋數 #{}",
            "NAT 表項數 #{}",
            "ARP 快取項數 #{}",
        ],
        0.0,
        1000.0,
    ),
]

_NOTES = [
    "",
    "",
    "",
    "",
    "定期監控",
    "異常偵測",
    "SLA 追蹤",
    "維運稽核",
    "效能基線",
]

_RECORDS_TABLE = sa.table(
    "records",
    sa.column("title", sa.String),
    sa.column("value", sa.Float),
    sa.column("category_id", sa.Integer),
    sa.column("created_by_principal_id", sa.Integer),
    sa.column("note", sa.String),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

_SEED_START = datetime(2026, 1, 20, 0, 0, 0, tzinfo=timezone.utc)
_SEED_WINDOW_SECS = 180 * 24 * 3600  # 180 天（秒）


_SEED_ADMIN_IN = ", ".join(f"'seed_admin_{i:02d}'" for i in range(1, 11))


def _build_rows(creator_pids: list[int]) -> list[dict]:
    rng = random.Random(42)
    rows: list[dict] = []
    for cat_id, titles, val_min, val_max in _CATEGORY_CFG:
        for i in range(75):
            title = titles[i % len(titles)].format(i + 1)
            value = round(rng.uniform(val_min, val_max), 2)
            note = _NOTES[rng.randrange(len(_NOTES))]
            delta = timedelta(seconds=rng.randint(0, _SEED_WINDOW_SECS))
            ts = _SEED_START + delta
            rows.append(
                {
                    "title": title,
                    "value": value,
                    "category_id": cat_id,
                    "created_by_principal_id": rng.choice(creator_pids),
                    "note": note,
                    "created_at": ts,
                    "updated_at": ts,
                }
            )
    rows.sort(key=lambda r: r["created_at"])
    return rows


def upgrade() -> None:
    bind = op.get_bind()

    result = bind.execute(
        sa.text(
            f"SELECT principal_id FROM admins"
            f" WHERE username IN ({_SEED_ADMIN_IN})"
            f" ORDER BY username"
        )
    ).fetchall()
    if not result:
        raise RuntimeError(
            "Seed 中止：找不到 seed admin（seed_admin_01～10）。\n"
            "請先執行 e1f2a3b4c5d6 migration，再升級至此。"
        )
    creator_pids = [row[0] for row in result]

    op.bulk_insert(_RECORDS_TABLE, _build_rows(creator_pids))


def downgrade() -> None:
    """⚠️ 清空整個 records 表。僅供開發 / 測試環境——生產環境請勿 downgrade 此 migration。"""
    op.execute(sa.text("DELETE FROM records"))
