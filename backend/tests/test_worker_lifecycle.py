from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import worker
from app.database import Base
from app.models import Contact, ContactStage, EmailStatus, ScheduledEmail, User
from app.schemas import CampaignCreateRequest
from app.services.campaigns import create_campaign
from app.services.contacts import get_or_create_personal_workspace


def make_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def make_campaign(engine):
    with Session(engine) as db:
        user = User(email="sender@example.com", google_credentials_encrypted="unused")
        db.add(user)
        db.commit()
        workspace = get_or_create_personal_workspace(db, user)
        contact = Contact(
            workspace_id=workspace.id,
            email="ada@example.com",
            normalized_email="ada@example.com",
            name="Ada",
            stage=ContactStage.qualified.value,
            tags=[],
        )
        db.add(contact)
        db.commit()
        campaign = create_campaign(
            db,
            user_id=user.id,
            workspace_id=workspace.id,
            request=CampaignCreateRequest(
                name="Lifecycle",
                contact_ids=[contact.id],
                subject="Hello",
                body="First",
                scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
                timezone="UTC",
            ),
        )
        job = campaign.recipients[0].scheduled_email
        job.status = EmailStatus.processing.value
        db.commit()
        return contact.id, job.id


def test_successful_campaign_send_marks_qualified_contact_contacted(monkeypatch) -> None:
    engine = make_engine()
    contact_id, job_id = make_campaign(engine)
    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(
        worker,
        "send_gmail_message",
        lambda *args, **kwargs: SimpleNamespace(message_id="message-1", thread_id="thread-1"),
    )

    worker.process_job(job_id)

    with Session(engine) as db:
        assert db.get(Contact, contact_id).stage == ContactStage.contacted.value
        assert db.get(ScheduledEmail, job_id).status == EmailStatus.sent.value


def test_worker_rechecks_suppression_before_send(monkeypatch) -> None:
    engine = make_engine()
    contact_id, job_id = make_campaign(engine)
    with Session(engine) as db:
        db.get(Contact, contact_id).stage = ContactStage.unsubscribed.value
        db.commit()
    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=engine))

    def fail_if_called(*args, **kwargs):
        raise AssertionError("email should not send")

    monkeypatch.setattr(worker, "send_gmail_message", fail_if_called)

    worker.process_job(job_id)

    with Session(engine) as db:
        job = db.get(ScheduledEmail, job_id)
        assert job.status == EmailStatus.cancelled.value
        assert "unsubscribed" in job.last_error
