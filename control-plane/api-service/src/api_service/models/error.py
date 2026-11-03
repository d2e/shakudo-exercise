"""The one shared error shape (F11)."""
from typing import Optional

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    field: str
    rule: str
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str = ""
    details: Optional[list[ErrorDetail]] = None


class Error(BaseModel):
    error: ErrorBody
