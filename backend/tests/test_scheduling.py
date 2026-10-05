from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import User
from app.schemas import ScheduleEmailRequest
from app.services.scheduling import create_schedule_batch, expand_schedule, to_utc


def test_expand_schedule_creates_expected_intervals() -> None:
    start = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
    values = expand_schedule(start, count=3, interval_minutes=60)
    assert values == [start, start + timedelta(hours=1), start + timedelta(hours=2)]


def test_to_utc_applies_user_timezone_to_naive_datetime() -> None:
    local = datetime(2026, 10, 5, 9, 0)
    assert to_utc(local, "America/New_York") == datetime(
        2026, 10, 5, 13, 0, tzinfo=timezone.utc
    )


def test_create_schedule_batch_persists_independent_jobs() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="sender@example.com", google_credentials_encrypted="unused")
        db.add(user)
        db.commit()
        db.refresh(user)

        request = ScheduleEmailRequest(
            recipient="recipient@example.com",
            subject="Hourly update",
            body="Hello",
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
            timezone="UTC",
            count=3,
            interval_minutes=60,
        )
        jobs = create_schedule_batch(db, user_id=user.id, request=request)

    assert len(jobs) == 3
    assert jobs[1].scheduled_at - jobs[0].scheduled_at == timedelta(hours=1)
    assert len({job.idempotency_key for job in jobs}) == 3

