from pydantic import BaseModel, Field, model_validator
from datetime import datetime
from typing import Optional


class AuthConfig(BaseModel):
    header: str = Field(min_length=1)
    values: list[str] = Field(min_length=1)


class AuthFailureResponse(BaseModel):
    status_code: int = Field(default=401, ge=100, le=599)
    headers: dict[str, str] = Field(
        default_factory=lambda: {"Content-Type": "application/json"}
    )
    body: str = '{"error":"unauthorized"}'


class ResponseRule(BaseModel):
    path: Optional[str] = None
    path_pattern: Optional[str] = None
    method: Optional[str] = None
    status_code: int = 200
    headers: dict[str, str] = Field(
        default_factory=lambda: {"Content-Type": "application/json"}
    )
    body: str = "{}"
    delay_ms: int = 0
    auth: Optional[AuthConfig] = None
    on_auth_failure: Optional[AuthFailureResponse] = None
    match: Optional[dict[str, str]] = None

    @model_validator(mode="after")
    def _on_auth_failure_requires_auth(self):
        if self.on_auth_failure is not None and self.auth is None:
            raise ValueError("on_auth_failure requires auth to be set")
        return self


class StoredRequest(BaseModel):
    id: int
    timestamp: datetime
    method: str
    path: str
    query_params: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: Optional[str] = None
    client_host: Optional[str] = None
    auth_status: Optional[str] = None
    auth_header: Optional[str] = None
    auth_values_count: Optional[int] = None
