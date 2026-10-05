from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class ParsedEmailIntent(BaseModel):
    action: Literal["schedule_email", "needs_clarification"]
    recipient: EmailStr | None = None
    subject: str | None = None
    body: str | None = None
    scheduled_at: str | None = Field(
        default=None,
        description="ISO 8601 local wall-clock datetime without a UTC offset"
    )
    count: int = Field(default=1, ge=1, le=100)
    interval_minutes: int = Field(default=0, ge=0, le=43_200)
    clarification_message: str | None = None


class ParseIntentRequest(BaseModel):
    message: str = Field(min_length=2, max_length=4_000)
    timezone: str = Field(default="UTC", max_length=64)


class ScheduleEmailRequest(BaseModel):
    recipient: EmailStr
    subject: str = Field(min_length=1, max_length=998)
    body: str = Field(min_length=1, max_length=100_000)
    scheduled_at: datetime
    timezone: str = Field(default="UTC", max_length=64)
    count: int = Field(default=1, ge=1, le=100)
    interval_minutes: int = Field(default=0, ge=0, le=43_200)

    @model_validator(mode="after")
    def validate_interval(self) -> "ScheduleEmailRequest":
        if self.count > 1 and self.interval_minutes < 1:
            raise ValueError("interval_minutes must be at least 1 when count is greater than 1")
        return self


class ScheduledEmailResponse(BaseModel):
    id: str
    recipient: str
    subject: str
    body: str
    scheduled_at: datetime
    original_timezone: str
    status: str
    retry_count: int
    last_error: str | None
    gmail_message_id: str | None
    sent_at: datetime | None
    created_at: datetime

    @field_validator("scheduled_at", "sent_at", "created_at", mode="before")
    @classmethod
    def mark_database_datetimes_as_utc(cls, value: datetime | None) -> datetime | None:
        # SQLite drops timezone metadata even though these columns store UTC.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    model_config = {"from_attributes": True}


class ScheduleBatchResponse(BaseModel):
    jobs: list[ScheduledEmailResponse]


class AuthStatusResponse(BaseModel):
    connected: bool
    email: str | None = None

