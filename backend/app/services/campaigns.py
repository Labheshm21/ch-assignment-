import hashlib
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    Campaign,
    CampaignDelivery,
    CampaignRecipient,
    CampaignRecipientState,
    CampaignStep,
    Contact,
    EmailStatus,
    ScheduledEmail,
    WorkspaceScheduledEmail,
)
from ..schemas import (
    CampaignCreateRequest,
    CampaignDeliveryResponse,
    CampaignRecipientResponse,
    CampaignResponse,
    CampaignStepResponse,
)
from .scheduling import expand_schedule, to_utc


TEMPLATE_VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
SUPPORTED_TEMPLATE_VARIABLES = {"first_name", "name", "email", "company", "job_title"}


def contact_template_values(contact: Contact) -> dict[str, str]:
    return {
        "first_name": contact.name.split()[0] if contact.name else "",
        "name": contact.name or "",
        "email": contact.email,
        "company": contact.company or "",
        "job_title": contact.job_title or "",
    }


def render_contact_template(template: str, contact: Contact) -> str:
    variables = set(TEMPLATE_VARIABLE_PATTERN.findall(template))
    unsupported = sorted(variables - SUPPORTED_TEMPLATE_VARIABLES)
    if unsupported:
        names = ", ".join(f"{{{{{name}}}}}" for name in unsupported)
        raise ValueError(f"Unsupported campaign variable: {names}")

    values = contact_template_values(contact)
    missing = sorted(name for name in variables if not values[name])
    if missing:
        names = ", ".join(f"{{{{{name}}}}}" for name in missing)
        raise ValueError(f"{contact.email} is missing data for {names}")
    return TEMPLATE_VARIABLE_PATTERN.sub(lambda match: values[match.group(1)], template)


def create_campaign(
    db: Session,
    *,
    user_id: str,
    workspace_id: str,
    request: CampaignCreateRequest,
) -> Campaign:
    start_utc = to_utc(request.scheduled_at, request.timezone)
    if start_utc < datetime.now(timezone.utc) - timedelta(seconds=5):
        raise ValueError("The scheduled time must be in the future")

    contacts = list(
        db.execute(
            select(Contact).where(
                Contact.workspace_id == workspace_id,
                Contact.id.in_(request.contact_ids),
            )
        ).scalars()
    )
    contacts_by_id = {contact.id: contact for contact in contacts}
    missing = [contact_id for contact_id in request.contact_ids if contact_id not in contacts_by_id]
    if missing:
        raise ValueError("One or more selected contacts do not belong to this workspace")

    steps = request.steps or []
    rendered_messages: dict[tuple[str, int], tuple[str, str]] = {}
    for contact_id in request.contact_ids:
        contact = contacts_by_id[contact_id]
        for step_position, step in enumerate(steps):
            rendered_messages[(contact_id, step_position)] = (
                render_contact_template(step.subject, contact),
                render_contact_template(step.body, contact),
            )

    campaign = Campaign(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        name=request.name,
        subject=request.subject or "",
        body=request.body or "",
        scheduled_at=start_utc,
        original_timezone=request.timezone,
        interval_minutes=request.interval_minutes,
    )
    db.add(campaign)
    db.flush()

    step_models: list[CampaignStep] = []
    for position, step in enumerate(steps):
        step_model = CampaignStep(
            campaign_id=campaign.id,
            position=position,
            subject=step.subject,
            body=step.body,
            delay_minutes=step.delay_minutes,
        )
        db.add(step_model)
        step_models.append(step_model)
    db.flush()

    schedule = expand_schedule(start_utc, len(request.contact_ids), request.interval_minutes)
    for position, (contact_id, scheduled_at) in enumerate(zip(request.contact_ids, schedule, strict=True)):
        contact = contacts_by_id[contact_id]
        jobs: list[ScheduledEmail] = []
        next_scheduled_at = scheduled_at
        for step_position, step_model in enumerate(step_models):
            if step_position:
                next_scheduled_at += timedelta(minutes=step_model.delay_minutes)
            rendered_subject, rendered_body = rendered_messages[(contact_id, step_position)]
            digest = hashlib.sha256(
                f"campaign|{campaign.id}|{contact.id}|step|{step_position}".encode()
            ).hexdigest()
            job = ScheduledEmail(
                user_id=user_id,
                recipient=contact.email,
                subject=rendered_subject,
                body=rendered_body,
                scheduled_at=next_scheduled_at,
                original_timezone=request.timezone,
                idempotency_key=digest,
            )
            db.add(job)
            jobs.append(job)
        db.flush()
        recipient = CampaignRecipient(
            campaign_id=campaign.id,
            contact_id=contact.id,
            scheduled_email_id=jobs[0].id,
            recipient_name=contact.name,
            recipient_email=contact.email,
            position=position,
        )
        db.add(recipient)
        db.flush()
        db.add(CampaignRecipientState(campaign_recipient_id=recipient.id))
        for step_position, (step_model, job) in enumerate(zip(step_models, jobs, strict=True)):
            db.add(WorkspaceScheduledEmail(scheduled_email_id=job.id, workspace_id=workspace_id))
            db.add(
                CampaignDelivery(
                    campaign_recipient_id=recipient.id,
                    step_id=step_model.id,
                    scheduled_email_id=job.id,
                    step_position=step_position,
                )
            )

    db.commit()
    created_campaign = get_campaign(db, campaign_id=campaign.id, workspace_id=workspace_id)
    if created_campaign is None:  # pragma: no cover - the insert and lookup share a transaction
        raise RuntimeError("Campaign could not be reloaded after creation")
    return created_campaign


def get_campaign(db: Session, *, campaign_id: str, workspace_id: str) -> Campaign | None:
    return db.execute(
        select(Campaign)
        .options(
            selectinload(Campaign.steps),
            selectinload(Campaign.recipients).selectinload(CampaignRecipient.scheduled_email),
            selectinload(Campaign.recipients)
            .selectinload(CampaignRecipient.deliveries)
            .selectinload(CampaignDelivery.scheduled_email),
            selectinload(Campaign.recipients).selectinload(CampaignRecipient.sequence_state),
        )
        .where(Campaign.id == campaign_id, Campaign.workspace_id == workspace_id)
    ).scalar_one_or_none()


def list_campaigns(db: Session, *, workspace_id: str) -> list[Campaign]:
    return list(
        db.execute(
            select(Campaign)
            .options(
                selectinload(Campaign.steps),
                selectinload(Campaign.recipients).selectinload(CampaignRecipient.scheduled_email),
                selectinload(Campaign.recipients)
                .selectinload(CampaignRecipient.deliveries)
                .selectinload(CampaignDelivery.scheduled_email),
                selectinload(Campaign.recipients).selectinload(CampaignRecipient.sequence_state),
            )
            .where(Campaign.workspace_id == workspace_id)
            .order_by(Campaign.created_at.desc())
            .limit(100)
        ).scalars()
    )


def campaign_status(status_counts: Counter[str]) -> str:
    total = sum(status_counts.values())
    if total and status_counts[EmailStatus.cancelled.value] == total:
        return "cancelled"
    terminal = status_counts[EmailStatus.sent.value] + status_counts[EmailStatus.failed.value] + status_counts[EmailStatus.cancelled.value]
    if total and terminal == total:
        return "completed" if status_counts[EmailStatus.failed.value] == 0 else "partial_failure"
    if status_counts[EmailStatus.processing.value] or terminal:
        return "in_progress"
    return "scheduled"


def serialize_campaign(campaign: Campaign) -> CampaignResponse:
    counts: Counter[str] = Counter()
    enrollment_counts: Counter[str] = Counter()
    recipients: list[CampaignRecipientResponse] = []
    for recipient in campaign.recipients:
        job = recipient.scheduled_email
        delivery_models = recipient.deliveries or []
        if delivery_models:
            deliveries = [
                CampaignDeliveryResponse(
                    id=delivery.id,
                    step_id=delivery.step_id,
                    step_position=delivery.step_position,
                    scheduled_email_id=delivery.scheduled_email.id,
                    subject=delivery.scheduled_email.subject,
                    body=delivery.scheduled_email.body,
                    scheduled_at=delivery.scheduled_email.scheduled_at,
                    status=delivery.scheduled_email.status,
                    retry_count=delivery.scheduled_email.retry_count,
                    last_error=delivery.scheduled_email.last_error,
                    gmail_message_id=delivery.scheduled_email.gmail_message_id,
                    sent_at=delivery.scheduled_email.sent_at,
                )
                for delivery in delivery_models
            ]
        else:
            deliveries = []
        for delivery_job in ([delivery.scheduled_email for delivery in delivery_models] or [job]):
            counts[delivery_job.status] += 1
        state = recipient.sequence_state
        sequence_status = state.status if state is not None else (
            "completed" if job.status == EmailStatus.sent.value else
            "failed" if job.status == EmailStatus.failed.value else
            "cancelled" if job.status == EmailStatus.cancelled.value else "active"
        )
        enrollment_counts[sequence_status] += 1
        recipients.append(
            CampaignRecipientResponse(
                id=recipient.id,
                contact_id=recipient.contact_id,
                scheduled_email_id=job.id,
                recipient_name=recipient.recipient_name,
                recipient_email=recipient.recipient_email,
                position=recipient.position,
                subject=job.subject,
                body=job.body,
                scheduled_at=job.scheduled_at,
                status=job.status,
                retry_count=job.retry_count,
                last_error=job.last_error,
                gmail_message_id=job.gmail_message_id,
                sent_at=job.sent_at,
                sequence_status=sequence_status,
                gmail_thread_id=state.gmail_thread_id if state is not None else None,
                replied_at=state.replied_at if state is not None else None,
                deliveries=deliveries,
            )
        )
    status_counts = {status.value: counts[status.value] for status in EmailStatus}
    return CampaignResponse(
        id=campaign.id,
        workspace_id=campaign.workspace_id,
        name=campaign.name,
        subject=campaign.subject,
        body=campaign.body,
        scheduled_at=campaign.scheduled_at,
        original_timezone=campaign.original_timezone,
        interval_minutes=campaign.interval_minutes,
        status=campaign_status(counts),
        recipient_count=len(recipients),
        status_counts=status_counts,
        enrollment_counts=dict(enrollment_counts),
        steps=[
            CampaignStepResponse(
                id=step.id,
                position=step.position,
                subject=step.subject,
                body=step.body,
                delay_minutes=step.delay_minutes,
            )
            for step in campaign.steps
        ],
        recipients=recipients,
        created_at=campaign.created_at,
    )


def cancel_campaign(campaign: Campaign) -> int:
    cancelled = 0
    for recipient in campaign.recipients:
        jobs = [delivery.scheduled_email for delivery in recipient.deliveries] or [recipient.scheduled_email]
        for job in jobs:
            if job.status == EmailStatus.pending.value:
                job.status = EmailStatus.cancelled.value
                cancelled += 1
        if recipient.sequence_state is not None and recipient.sequence_state.status == "active":
            recipient.sequence_state.status = "cancelled"
    return cancelled


def mark_recipient_replied(
    db: Session, *, recipient_id: str, replied_at: datetime
) -> int:
    """Stop one enrollment and cancel every follow-up that has not started sending."""
    state = db.get(CampaignRecipientState, recipient_id)
    if state is None or state.status != "active":
        return 0
    state.status = "replied"
    state.replied_at = replied_at
    pending_jobs = db.execute(
        select(ScheduledEmail)
        .join(CampaignDelivery, CampaignDelivery.scheduled_email_id == ScheduledEmail.id)
        .where(
            CampaignDelivery.campaign_recipient_id == recipient_id,
            ScheduledEmail.status == EmailStatus.pending.value,
        )
    ).scalars()
    cancelled = 0
    for pending_job in pending_jobs:
        pending_job.status = EmailStatus.cancelled.value
        pending_job.last_error = "Cancelled automatically after recipient reply"
        cancelled += 1
    return cancelled
