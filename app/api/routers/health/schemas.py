from pydantic import BaseModel, Field
from typing import Any

class HealthResponse(BaseModel):
    message: str = Field(description="Health status message")
    app_version: str = Field(description="Current application version")

class TestErrorResponse(BaseModel):
    status: str = Field(description="Test result status")

class ErrorResponse(BaseModel):
    error: str = Field(description="Machine-readable error code")
    message: str = Field(description="Human-readable error message")
    request_id: str = Field(description="Request ID for correlating logs")
    details: dict[str, Any] | None = Field(
        default=None,
        description="Additional error details (validation errors, field info, etc.)",
    )
    debug_info: dict[str, Any] | None = Field(
        default=None,
        description="Detailed debug info (only present when app_debug=True)"
    )