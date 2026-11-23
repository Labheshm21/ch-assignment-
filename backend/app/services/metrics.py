from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Campaign,
    CampaignDelivery,
    CampaignRecipient,
    CampaignRecipientState,
    Contact,
    ContactStage,
    EmailStatus,
    ScheduledEmail,
)
from ..schemas import GtmMetricsResponse


def _rate(value: int, total: int) -> float:
    return round(value * 100 / total, 1) if total else 0.0


def get_gtm_metrics(db: Session, *, workspace_id: str) -> GtmMetricsResponse:
    stage_counts = dict(
        db.execute(
            select(Contact.stage, func.count(Contact.id))
            .where(Contact.workspace_id == workspace_id)
            .group_by(Contact.stage)
        ).all()
    )
    contacts = sum(stage_counts.values())
    campaigns = db.scalar(
        select(func.count(Campaign.id)).where(Campaign.workspace_id == workspace_id)
    ) or 0
    emails_sent = db.scalar(
        select(func.count(ScheduledEmail.id))
        .select_from(ScheduledEmail)
        .join(CampaignDelivery, CampaignDelivery.scheduled_email_id == ScheduledEmail.id)
        .join(CampaignRecipient, CampaignRecipient.id == CampaignDelivery.campaign_recipient_id)
        .join(Campaign, Campaign.id == CampaignRecipient.campaign_id)
        .where(
            Campaign.workspace_id == workspace_id,
            ScheduledEmail.status == EmailStatus.sent.value,
        )
    ) or 0
    recipients_contacted = db.scalar(
        select(func.count(func.distinct(CampaignRecipient.id)))
        .select_from(CampaignRecipient)
        .join(Campaign, Campaign.id == CampaignRecipient.campaign_id)
        .join(CampaignDelivery, CampaignDelivery.campaign_recipient_id == CampaignRecipient.id)
        .join(ScheduledEmail, ScheduledEmail.id == CampaignDelivery.scheduled_email_id)
        .where(
            Campaign.workspace_id == workspace_id,
            ScheduledEmail.status == EmailStatus.sent.value,
        )
    ) or 0
    replies = db.scalar(
        select(func.count(CampaignRecipientState.campaign_recipient_id))
        .select_from(CampaignRecipientState)
        .join(
            CampaignRecipient,
            CampaignRecipient.id == CampaignRecipientState.campaign_recipient_id,
        )
        .join(Campaign, Campaign.id == CampaignRecipient.campaign_id)
        .where(
            Campaign.workspace_id == workspace_id,
            CampaignRecipientState.replied_at.is_not(None),
        )
    ) or 0
    interested = sum(
        stage_counts.get(stage, 0)
        for stage in (
            ContactStage.interested.value,
            ContactStage.meeting.value,
            ContactStage.won.value,
        )
    )
    meetings = sum(
        stage_counts.get(stage, 0)
        for stage in (ContactStage.meeting.value, ContactStage.won.value)
    )

    return GtmMetricsResponse(
        contacts=contacts,
        needs_review=stage_counts.get(ContactStage.new.value, 0),
        qualified=stage_counts.get(ContactStage.qualified.value, 0),
        rejected=stage_counts.get(ContactStage.rejected.value, 0),
        campaigns=campaigns,
        emails_sent=emails_sent,
        recipients_contacted=recipients_contacted,
        replies=replies,
        interested=interested,
        meetings=meetings,
        reply_rate=_rate(replies, recipients_contacted),
        interest_rate=_rate(interested, recipients_contacted),
        meeting_rate=_rate(meetings, recipients_contacted),
    )
