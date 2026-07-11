# uuid4 為 128-bit 整數值（16 bytes），其中 122 bits 是隨機（剩 6 bits 是版本/variant 標記）。字串表示法是 36 字元
from contextvars import Token
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.context import request_id_ctx

# 搬移的時機：將來多個模組也要 import 時，搬到 app/core/constants.py
REQUEST_ID_HEADER: str = "X-Request-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    從 incoming header 取 request_id（沒有就生成 UUID4），
    塞入 ContextVar 讓全域可讀並在 response header 回傳供 client 對應
    """

    def __init__(self, app: ASGIApp, header_name: str = REQUEST_ID_HEADER) -> None:
        super().__init__(app)
        self.header_name: str = header_name

    # call_next：把 request 交給下一個 middleware 或是 endpoint
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id: str = request.headers.get(self.header_name) or str(uuid4())

        # Token 的唯一用途是給 reset(token) 用 — 把 ContextVar 恢復到 set 之前的狀態
        token: Token[str] = request_id_ctx.set(request_id)
        try:
            # API 處理請求
            response: Response = await call_next(request)
        finally:
            # reset 維持 set/reset 對稱性。
            # 雖然 asyncio.Task 已自動隔離 request 間 context（不 reset 也不會跨 request 洩漏），
            # reset 為防禦性動作 + 同 task 內若有 sub-context 才不會亂
            request_id_ctx.reset(token)

        response.headers[self.header_name] = request_id
        return response
