import pytest

from app.schemas import ParsedEmailIntent
from app.services.intent import _normalize_local_wall_time, _parse_json_object, _validated_intent


def test_parse_json_object_accepts_plain_json() -> None:
    assert _parse_json_object('{"action":"needs_clarification"}') == {
        "action": "needs_clarification"
    }


def test_parse_json_object_ignores_markdown_wrapper() -> None:
    content = '```json\n{"action":"needs_clarification"}\n```'
    assert _parse_json_object(content)["action"] == "needs_clarification"


def test_parse_json_object_rejects_missing_json() -> None:
    with pytest.raises(RuntimeError):
        _parse_json_object("No structured response was produced")


def test_normalize_local_wall_time_removes_incorrect_model_offset() -> None:
    intent = ParsedEmailIntent(
        action="schedule_email",
        recipient="recipient@example.com",
        subject="Test",
        body="Hello",
        scheduled_at="2026-10-05T16:37:00Z",
        count=1,
        interval_minutes=0,
        clarification_message=None,
    )

    normalized = _normalize_local_wall_time(intent)

    assert normalized.scheduled_at == "2026-10-05T16:37:00"


def test_missing_fields_force_clarification() -> None:
    intent = _validated_intent(
        {
            "action": "schedule_email",
            "recipient": "recipient@example.com",
            "subject": None,
            "body": "Hello",
            "scheduled_at": None,
            "count": 1,
            "interval_minutes": 0,
            "clarification_message": None,
        }
    )

    assert intent.action == "needs_clarification"
    assert intent.clarification_message == "Please provide the subject and send time."


def test_recipient_name_without_email_forces_clarification() -> None:
    intent = _validated_intent(
        {
            "action": "schedule_email",
            "recipient": "Alice",
            "subject": "Hello",
            "body": "Checking in",
            "scheduled_at": "2026-10-05T16:37:00",
            "count": 1,
            "interval_minutes": 0,
            "clarification_message": None,
        }
    )

    assert intent.action == "needs_clarification"
    assert intent.clarification_message == "Please provide the recipient email address."


def test_omitted_and_null_optional_fields_force_clarification() -> None:
    intent = _validated_intent(
        {
            "action": "needs_clarification",
            "recipient": None,
            "count": None,
            "interval_minutes": None,
        }
    )

    assert intent.action == "needs_clarification"
    assert intent.count == 1
    assert intent.interval_minutes == 0
    assert "recipient email address" in (intent.clarification_message or "")
    assert "send time" in (intent.clarification_message or "")
