import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from ..models import ScheduledEmail
from ..schemas import ScheduleEmailRequest


def to_utc(value: datetime, timezone_name: str) -> datetime:
    if value.tzinfo is None:
        try:
            value = value.replace(tzinfo=ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {timezone_name}") from exc
    return value.astimezone(timezone.utc)


def expand_schedule(start: datetime, count: int, interval_minutes: int) -> list[datetime]:
    return [start + timedelta(minutes=index * interval_minutes) for index in range(count)]


def create_schedule_batch(
    db: Session, *, user_id: str, request: ScheduleEmailRequest
) -> list[ScheduledEmail]:
    start_utc = to_utc(request.scheduled_at, request.timezone)
    now = datetime.now(timezone.utc)
    if start_utc < now - timedelta(seconds=5):
        raise ValueError("The scheduled time must be in the future")

    jobs: list[ScheduledEmail] = []
    batch_seed = f"{user_id}|{request.recipient}|{request.subject}|{request.body}|{start_utc.isoformat()}"
    for index, scheduled_at in enumerate(
        expand_schedule(start_utc, request.count, request.interval_minutes)
    ):
        digest = hashlib.sha256(f"{batch_seed}|{index}".encode()).hexdigest()
        job = ScheduledEmail(
            user_id=user_id,
            recipient=str(request.recipient),
            subject=request.subject,
            body=request.body,
            scheduled_at=scheduled_at,
            original_timezone=request.timezone,
            idempotency_key=digest,
        )
        db.add(job)
        jobs.append(job)

    db.commit()
    for job in jobs:
        db.refresh(job)
    return jobs

