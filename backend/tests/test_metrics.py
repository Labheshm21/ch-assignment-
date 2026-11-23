from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Contact, ContactStage, EmailStatus, User
from app.schemas import CampaignCreateRequest
from app.services.campaigns import create_campaign
from app.services.contacts import get_or_create_personal_workspace
from app.services.metrics import get_gtm_metrics


def test_gtm_metrics_are_workspace_scoped_and_use_recipient_funnel_rates() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="owner@example.com", google_credentials_encrypted="unused")
        db.add(user)
        db.commit()
        workspace = get_or_create_personal_workspace(db, user)
        qualified = Contact(
            workspace_id=workspace.id,
            email="ada@example.com",
            normalized_email="ada@example.com",
            name="Ada",
            stage=ContactStage.qualified.value,
            tags=[],
        )
        review = Contact(
            workspace_id=workspace.id,
            email="grace@example.com",
            normalized_email="grace@example.com",
            name="Grace",
            tags=[],
        )
        db.add_all([qualified, review])
        db.commit()
        campaign = create_campaign(
            db,
            user_id=user.id,
            workspace_id=workspace.id,
            request=CampaignCreateRequest(
                name="Founders",
                contact_ids=[qualified.id],
                subject="Hello",
                body="First",
                scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
                timezone="UTC",
            ),
        )
        recipient = campaign.recipients[0]
        recipient.scheduled_email.status = EmailStatus.sent.value
        recipient.sequence_state.replied_at = datetime.now(timezone.utc)
        qualified.stage = ContactStage.meeting.value
        db.commit()

        metrics = get_gtm_metrics(db, workspace_id=workspace.id)

        assert metrics.contacts == 2
        assert metrics.needs_review == 1
        assert metrics.qualified == 0
        assert metrics.campaigns == 1
        assert metrics.emails_sent == 1
        assert metrics.recipients_contacted == 1
        assert metrics.replies == 1
        assert metrics.interested == 1
        assert metrics.meetings == 1
        assert metrics.reply_rate == 100.0
        assert metrics.interest_rate == 100.0
        assert metrics.meeting_rate == 100.0
