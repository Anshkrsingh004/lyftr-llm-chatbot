"""Pydantic request models and validation rules for the chat API."""
from typing import Optional

from pydantic import BaseModel, field_validator


class ChatRequest(BaseModel):
    """Body for POST /chat.

    Validation failures raise ValueError, which FastAPI surfaces as a 422 with a
    `detail` field (the default FastAPI shape, which the contract allows).
    """

    session_id: str
    message: str
    system_prompt: Optional[str] = None

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: str) -> str:
        if not isinstance(value, str) or len(value) < 1 or len(value) > 128 or not value.strip():
            raise ValueError("session_id must be 1-128 non-empty characters")
        return value

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("message is required")
        trimmed = value.strip()
        if len(trimmed) < 1:
            raise ValueError("message must not be empty after trimming whitespace")
        if len(trimmed) > 4000:
            raise ValueError("message must be at most 4000 characters after trimming")
        return trimmed
