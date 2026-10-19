import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from ..models import User
from ..security import CredentialCipher
from .oauth import serialize_credentials


def _credentials_for_user(db: Session, user: User) -> Credentials:
    cipher = CredentialCipher()
    payload = cipher.decrypt(user.google_credentials_encrypted)
    credentials = Credentials(**payload)

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        user.google_credentials_encrypted = cipher.encrypt(serialize_credentials(credentials))
        db.add(user)
        db.commit()

    return credentials


def build_raw_message(*, sender: str, recipient: str, subject: str, body: str) -> str:
    message = EmailMessage()
    message["To"] = recipient
    message["From"] = sender
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


@dataclass(frozen=True)
class GmailSendResult:
    message_id: str
    thread_id: str | None


def send_gmail_message(
    db: Session,
    *,
    user: User,
    recipient: str,
    subject: str,
    body: str,
    thread_id: str | None = None,
) -> GmailSendResult:
    credentials = _credentials_for_user(db, user)
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    raw = build_raw_message(
        sender=user.email,
        recipient=recipient,
        subject=subject,
        body=body,
    )
    request_body = {"raw": raw}
    if thread_id is not None:
        request_body["threadId"] = thread_id
    result = service.users().messages().send(userId="me", body=request_body).execute()
    return GmailSendResult(message_id=result["id"], thread_id=result.get("threadId"))


def find_reply_in_thread(
    db: Session,
    *,
    user: User,
    thread_id: str,
    recipient_email: str,
    after: datetime | None,
) -> datetime | None:
    """Return the first human reply time from the campaign recipient, if present."""
    credentials = _credentials_for_user(db, user)
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    thread = (
        service.users()
        .threads()
        .get(
            userId="me",
            id=thread_id,
            format="metadata",
            metadataHeaders=["From", "Auto-Submitted"],
        )
        .execute()
    )
    target = recipient_email.casefold()
    after_ms = int(after.timestamp() * 1_000) if after is not None else 0
    for message in thread.get("messages", []):
        received_ms = int(message.get("internalDate", "0"))
        if received_ms <= after_ms:
            continue
        headers = {
            header.get("name", "").casefold(): header.get("value", "")
            for header in message.get("payload", {}).get("headers", [])
        }
        sender = parseaddr(headers.get("from", ""))[1].casefold()
        auto_submitted = headers.get("auto-submitted", "").casefold()
        if sender == target and auto_submitted in {"", "no"}:
            return datetime.fromtimestamp(received_ms / 1_000, tz=timezone.utc)
    return None

