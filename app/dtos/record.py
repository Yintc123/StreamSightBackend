"""Record domain DTOs — framework-agnostic（無 FastAPI / SQLAlchemy import）。

service 收這些 DTO、router body 直接用同一型別（UserService 風格，records-service.md §2.7）。
`RowError`/`ImportResult` 為 service 回傳型別，放此避免 service import router（分層倒置）。
"""

from pydantic import BaseModel, Field


class RecordCreate(BaseModel):
    """建立 record 的四個可編輯欄位（title/value/category/note）。"""

    title: str = Field(min_length=1, max_length=200)
    value: float = Field(allow_inf_nan=False)  # NaN/Inf 會毒化排序/顯示 → 422
    category: str = Field(
        min_length=1, max_length=20
    )  # 分類「名」，service 解析成 id（要求 active）
    note: str = Field(default="", max_length=500)


class RecordUpdate(RecordCreate):
    """update 全量替換四欄（records-api.md §4.2），欄位同 create。"""


class RowError(BaseModel):
    """匯入單列錯誤（前端顯示時 row_index+1，data-source.md §匯入）。"""

    row_index: int  # 0-based，對應輸入列序
    reason: str


class ImportResult(BaseModel):
    """匯入結果（部分成功語意：created 落地數 + 逐列 errors）。"""

    created: int
    errors: list[RowError]
