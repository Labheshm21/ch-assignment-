from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CampaignRecipient, Contact, EmailStatus, ScheduledEmail, User, Workspace, WorkspaceMember
from app.schemas import CampaignCreateRequest
from app.services.campaigns import (
    cancel_campaign,
    create_campaign,
    render_contact_template,
    serialize_campaign,
)
from app.services.contacts import get_or_create_personal_workspace


def make_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def add_contact(db: Session, workspace_id: str, email: str, name: str) -> Contact:
    contact = Contact(
        workspace_id=workspace_id,
        email=email,
        normalized_email=email.casefold(),
        name=name,
        tags=[],
    )
    db.add(contact)
    db.flush()
    return contact


def test_create_campaign_schedules_one_workspace_job_per_contact() -> None:
    with Session(make_engine()) as db:
        user = User(email="sender@example.com", google_credentials_encrypted="unused")
        db.add(user)
        db.commit()
        workspace = get_or_create_personal_workspace(db, user)
        first = add_contact(db, workspace.id, "ada@example.com", "Ada")
        second = add_contact(db, workspace.id, "grace@example.com", "Grace")
        db.commit()
        start = datetime.now(timezone.utc) + timedelta(hours=1)

        campaign = create_campaign(
            db,
            user_id=user.id,
            workspace_id=workspace.id,
            request=CampaignCreateRequest(
                name="Founders",
                contact_ids=[second.id, first.id],
                subject="Hello",
                body="A workspace update",
                scheduled_at=start,
                timezone="UTC",
                interval_minutes=15,
            ),
        )
        response = serialize_campaign(campaign)

        assert response.workspace_id == workspace.id
        assert response.status == "scheduled"
        assert [recipient.recipient_email for recipient in response.recipients] == [
            "grace@example.com",
            "ada@example.com",
        ]
        assert response.recipients[1].scheduled_at - response.recipients[0].scheduled_at == timedelta(minutes=15)
        assert response.status_counts == {
            "pending": 2,
            "processing": 0,
            "sent": 0,
            "failed": 0,
            "cancelled": 0,
        }
        assert db.scalar(select(func.count()).select_from(ScheduledEmail)) == 2
        assert db.scalar(select(func.count()).select_from(CampaignRecipient)) == 2


def test_create_campaign_rejects_contacts_from_another_workspace() -> None:
    with Session(make_engine()) as db:
        user = User(email="sender@example.com", google_credentials_encrypted="unused")
        db.add(user)
        db.commit()
        first_workspace = get_or_create_personal_workspace(db, user)
        second_workspace = Workspace(name="Other client")
        db.add(second_workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=second_workspace.id, user_id=user.id, role="owner"))
        outsider = add_contact(db, second_workspace.id, "outside@example.com", "Outside")
        db.commit()

        with pytest.raises(ValueError, match="do not belong"):
            create_campaign(
                db,
                user_id=user.id,
                workspace_id=first_workspace.id,
                request=CampaignCreateRequest(
                    name="Invalid campaign",
                    contact_ids=[outsider.id],
                    subject="Hello",
                    body="No cross-workspace sends",
                    scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
                    timezone="UTC",
                ),
            )

        assert db.scalar(select(func.count()).select_from(ScheduledEmail)) == 0


def test_campaign_status_tracks_each_recipient_and_cancels_only_pending_jobs() -> None:
    with Session(make_engine()) as db:
        user = User(email="sender@example.com", google_credentials_encrypted="unused")
        db.add(user)
        db.commit()
        workspace = get_or_create_personal_workspace(db, user)
        first = add_contact(db, workspace.id, "sent@example.com", "Sent")
        second = add_contact(db, workspace.id, "waiting@example.com", "Waiting")
        db.commit()
        campaign = create_campaign(
            db,
            user_id=user.id,
            workspace_id=workspace.id,
            request=CampaignCreateRequest(
                name="Mixed state",
                contact_ids=[first.id, second.id],
                subject="Status",
                body="Track me",
                scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
                timezone="UTC",
            ),
        )
        campaign.recipients[0].scheduled_email.status = EmailStatus.sent.value
        campaign.recipients[0].scheduled_email.gmail_message_id = "gmail-123"
        campaign.recipients[0].scheduled_email.sent_at = datetime.now(timezone.utc)

        cancelled = cancel_campaign(campaign)
        db.commit()
        response = serialize_campaign(campaign)

        assert cancelled == 1
        assert response.status == "completed"
        assert response.status_counts["sent"] == 1
        assert response.status_counts["cancelled"] == 1
        assert response.recipients[0].gmail_message_id == "gmail-123"
        assert response.recipients[1].status == "cancelled"


def test_campaign_personalizes_subject_and_body_for_each_contact() -> None:
    with Session(make_engine()) as db:
        user = User(email="sender@example.com", google_credentials_encrypted="unused")
        db.add(user)
        db.commit()
        workspace = get_or_create_personal_workspace(db, user)
        first = add_contact(db, workspace.id, "ada@example.com", "Ada Lovelace")
        first.company = "Analytical Engines"
        first.job_title = "Founder"
        second = add_contact(db, workspace.id, "grace@example.com", "Grace Hopper")
        second.company = "US Navy"
        second.job_title = "Rear Admiral"
        db.commit()

        campaign = create_campaign(
            db,
            user_id=user.id,
            workspace_id=workspace.id,
            request=CampaignCreateRequest(
                name="Personalized",
                contact_ids=[first.id, second.id],
                subject="Hello {{ first_name }}",
                body="Hi {{first_name}}, following up about {{company}} and your work as {{job_title}}.",
                scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
                timezone="UTC",
            ),
        )
        response = serialize_campaign(campaign)

        assert response.subject == "Hello {{ first_name }}"
        assert response.recipients[0].subject == "Hello Ada"
        assert response.recipients[0].body == (
            "Hi Ada, following up about Analytical Engines and your work as Founder."
        )
        assert response.recipients[1].subject == "Hello Grace"
        assert "US Navy" in response.recipients[1].body


def test_campaign_rejects_missing_or_unknown_template_variables() -> None:
    contact = Contact(
        workspace_id="workspace-id",
        email="ada@example.com",
        normalized_email="ada@example.com",
        name="Ada Lovelace",
        tags=[],
    )

    with pytest.raises(ValueError, match="missing data.*company"):
        render_contact_template("Hello {{first_name}} from {{company}}", contact)
    with pytest.raises(ValueError, match="Unsupported.*nickname"):
        render_contact_template("Hello {{nickname}}", contact)
