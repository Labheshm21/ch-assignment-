import base64
from datetime import datetime, timezone
from email import message_from_bytes
from types import SimpleNamespace

from app.services import gmail
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


def test_legacy_credentials_refresh_once_and_persist_expiry(monkeypatch) -> None:
    encrypted_payloads = []

    class FakeCipher:
        def decrypt(self, token):
            return {"token": "stale", "refresh_token": "refresh-token"}

        def encrypt(self, payload):
            encrypted_payloads.append(payload)
            return "updated-encrypted-credentials"

    credentials = SimpleNamespace(
        token="stale",
        refresh_token="refresh-token",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client-id",
        client_secret="client-secret",
        scopes=["scope"],
        expiry=None,
        expired=False,
    )

    def refresh(_request):
        credentials.token = "fresh"
        credentials.expiry = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)

    credentials.refresh = refresh
    db = SimpleNamespace(add=lambda user: None, commit=lambda: None)
    user = SimpleNamespace(google_credentials_encrypted="old-encrypted-credentials")
    monkeypatch.setattr(gmail, "CredentialCipher", FakeCipher)
    monkeypatch.setattr(gmail, "credentials_from_payload", lambda payload: credentials)

    restored = gmail._credentials_for_user(db, user)

    assert restored.token == "fresh"
    assert user.google_credentials_encrypted == "updated-encrypted-credentials"
    assert encrypted_payloads[0]["token"] == "fresh"
    assert encrypted_payloads[0]["expiry"] == "2026-11-18T12:00:00"

