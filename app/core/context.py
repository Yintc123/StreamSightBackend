"""
ContextVar for request_id across request scope.
default 是 "-"，讓 request context 外的 log 仍能正常輸出(不會 LookupError)
啟動期、background task 等非 request 場景，filter 取值不會炸，輸出 [-] 也讓人一眼分辨「這不是 request log」
"""

from contextvars import ContextVar

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")
