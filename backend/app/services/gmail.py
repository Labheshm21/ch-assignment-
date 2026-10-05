import base64
from email.message import EmailMessage

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


def send_gmail_message(
    db: Session, *, user: User, recipient: str, subject: str, body: str
) -> str:
    credentials = _credentials_for_user(db, user)
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    raw = build_raw_message(
        sender=user.email,
        recipient=recipient,
        subject=subject,
        body=body,
    )
    result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return result["id"]

