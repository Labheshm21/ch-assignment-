import base64
from email import message_from_bytes

from app.services.gmail import build_raw_message


def test_build_raw_message() -> None:
    raw = build_raw_message(
        sender="sender@example.com",
        recipient="recipient@example.com",
        subject="A useful subject",
        body="Hello from the scheduler.",
    )
    message = message_from_bytes(base64.urlsafe_b64decode(raw.encode()))
    assert message["To"] == "recipient@example.com"
    assert message["From"] == "sender@example.com"
    assert message["Subject"] == "A useful subject"
    assert "Hello from the scheduler." in message.get_payload()

