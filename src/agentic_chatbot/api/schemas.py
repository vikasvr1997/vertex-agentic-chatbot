"""Pydantic request/response models for the internal chat API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    session_id: str | None = Field(default=None, description="Omit to start a new session.")


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    turn_count: int


class HealthResponse(BaseModel):
    status: str
    environment: str
