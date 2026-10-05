from datetime import datetime, timezone

from app.schemas import ScheduledEmailResponse


def test_scheduled_email_response_marks_sqlite_datetimes_as_utc() -> None:
    response = ScheduledEmailResponse(
        id="job-id",
        recipient="recipient@example.com",
        subject="Test",
        body="Hello",
        scheduled_at=datetime(2026, 10, 5, 20, 37),
        original_timezone="America/New_York",
        status="pending",
        retry_count=0,
        last_error=None,
        gmail_message_id=None,
        sent_at=None,
        created_at=datetime(2026, 10, 5, 16, 30),
    )

    assert response.scheduled_at.tzinfo == timezone.utc
    assert response.model_dump(mode="json")["scheduled_at"].endswith("Z")
