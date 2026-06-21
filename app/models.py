from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class StoredRequest(BaseModel):
    id: int
    timestamp: datetime
    method: str
    path: str
    query_params: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: Optional[str] = None
    client_host: Optional[str] = None


class ResponseRule(BaseModel):
    path: Optional[str] = None
    path_pattern: Optional[str] = None
    method: Optional[str] = None
    status_code: int = 200
    headers: dict[str, str] = Field(default_factory=lambda: {"Content-Type": "application/json"})
    body: str = "{}"
    delay_ms: int = 0
