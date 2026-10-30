import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RunCreate(BaseModel):
    task: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    max_tokens: int | None = Field(default=None, gt=0)


class StepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step_no: int
    tool: str
    input: dict
    output: dict | None
    status: str
    attempts: int
    tokens: int | None
    error: str | None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task: str
    idempotency_key: str
    status: str
    error: str | None
    result: str | None
    max_tokens: int | None
    tokens_used: int
    created_at: datetime
    updated_at: datetime
    steps: list[StepOut] = []
