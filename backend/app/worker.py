import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from .config import get_settings
from .database import Base, SessionLocal, engine
from .models import (
    CampaignDelivery,
    CampaignDeliveryThread,
    CampaignRecipient,
    CampaignRecipientState,
    EmailStatus,
    ScheduledEmail,
    User,
)
from .services.gmail import find_reply_in_thread, send_gmail_message
from .services.campaigns import mark_recipient_replied


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("chronos-worker")


def recipient_thread_ids(db, recipient_id: str) -> list[str]:
    return list(
        db.execute(
            select(CampaignDeliveryThread.gmail_thread_id)
            .join(
                CampaignDelivery,
                CampaignDelivery.id == CampaignDeliveryThread.campaign_delivery_id,
            )
            .where(CampaignDelivery.campaign_recipient_id == recipient_id)
            .order_by(CampaignDelivery.step_position)
        ).scalars()
    )


def first_delivery_sent_at(db, recipient_id: str) -> datetime | None:
    return db.scalar(
        select(ScheduledEmail.sent_at)
        .join(CampaignDelivery, CampaignDelivery.scheduled_email_id == ScheduledEmail.id)
        .where(CampaignDelivery.campaign_recipient_id == recipient_id)
        .order_by(CampaignDelivery.step_position)
        .limit(1)
    )


def find_recipient_reply(db, *, user: User, recipient: CampaignRecipient) -> datetime | None:
    first_sent_at = first_delivery_sent_at(db, recipient.id)
    for thread_id in recipient_thread_ids(db, recipient.id):
        replied_at = find_reply_in_thread(
            db,
            user=user,
            thread_id=thread_id,
            recipient_email=recipient.recipient_email,
            after=first_sent_at,
        )
        if replied_at is not None:
            return replied_at
    return None


def claim_due_job() -> str | None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(minutes=settings.worker_lock_timeout_minutes)

    with SessionLocal() as db:
        query = (
            select(ScheduledEmail)
            .where(
                ScheduledEmail.scheduled_at <= now,
                or_(
                    ScheduledEmail.status == EmailStatus.pending.value,
                    (
                        (ScheduledEmail.status == EmailStatus.processing.value)
                        & (ScheduledEmail.locked_at < stale_before)
                    ),
                ),
            )
            .order_by(ScheduledEmail.scheduled_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        job = db.execute(query).scalar_one_or_none()
        if job is None:
            return None
        job.status = EmailStatus.processing.value
        job.locked_at = now
        db.commit()
        return job.id


def process_job(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(ScheduledEmail, job_id)
        if job is None or job.status != EmailStatus.processing.value:
            return
        delivery = db.execute(
            select(CampaignDelivery).where(CampaignDelivery.scheduled_email_id == job.id)
        ).scalar_one_or_none()
        state = (
            db.get(CampaignRecipientState, delivery.campaign_recipient_id)
            if delivery is not None
            else None
        )
        if state is not None and state.status != "active":
            job.status = EmailStatus.cancelled.value
            job.locked_at = None
            job.last_error = f"Sequence stopped: {state.status}"
            db.commit()
            return
        user = db.get(User, job.user_id)
        if user is None:
            job.status = EmailStatus.failed.value
            job.last_error = "Connected Gmail user no longer exists"
            db.commit()
            return

        if delivery is not None and state is not None and delivery.step_position > 0:
            recipient = db.get(CampaignRecipient, delivery.campaign_recipient_id)
            try:
                replied_at = (
                    find_recipient_reply(db, user=user, recipient=recipient)
                    if recipient is not None
                    else None
                )
            except Exception as exc:
                logger.exception("Reply guard failed for follow-up job %s", job.id)
                job.status = EmailStatus.pending.value
                job.scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=5)
                job.locked_at = None
                job.last_error = f"Waiting for Gmail reply check: {str(exc)[:1_900]}"
                db.commit()
                return
            if replied_at is not None:
                mark_recipient_replied(db, recipient_id=delivery.campaign_recipient_id, replied_at=replied_at)
                job.status = EmailStatus.cancelled.value
                job.locked_at = None
                job.last_error = "Cancelled automatically after recipient reply"
                db.commit()
                return

        try:
            send_result = send_gmail_message(
                db,
                user=user,
                recipient=job.recipient,
                subject=job.subject,
                body=job.body,
            )
        except Exception as exc:  # worker boundary: log and persist unexpected provider failures
            logger.exception("Failed to send job %s", job.id)
            job.retry_count += 1
            job.last_error = str(exc)[:2_000]
            job.locked_at = None
            if job.retry_count >= job.max_retries:
                job.status = EmailStatus.failed.value
                if state is not None and state.status == "active":
                    state.status = "failed"
                    later_jobs = db.execute(
                        select(ScheduledEmail)
                        .join(
                            CampaignDelivery,
                            CampaignDelivery.scheduled_email_id == ScheduledEmail.id,
                        )
                        .where(
                            CampaignDelivery.campaign_recipient_id
                            == delivery.campaign_recipient_id,
                            CampaignDelivery.step_position > delivery.step_position,
                            ScheduledEmail.status == EmailStatus.pending.value,
                        )
                    ).scalars()
                    for later_job in later_jobs:
                        later_job.status = EmailStatus.cancelled.value
                        later_job.last_error = "Cancelled because an earlier sequence step failed"
            else:
                job.status = EmailStatus.pending.value
                job.scheduled_at = datetime.now(timezone.utc) + timedelta(
                    minutes=2 ** (job.retry_count - 1)
                )
            db.commit()
            return

        job.status = EmailStatus.sent.value
        job.gmail_message_id = send_result.message_id
        job.sent_at = datetime.now(timezone.utc)
        job.locked_at = None
        job.last_error = None
        if state is not None:
            state.gmail_thread_id = state.gmail_thread_id or send_result.thread_id
            if delivery is not None and send_result.thread_id is not None:
                db.add(
                    CampaignDeliveryThread(
                        campaign_delivery_id=delivery.id,
                        gmail_thread_id=send_result.thread_id,
                    )
                )
            later_jobs = db.scalar(
                select(ScheduledEmail.id)
                .join(CampaignDelivery, CampaignDelivery.scheduled_email_id == ScheduledEmail.id)
                .where(
                    CampaignDelivery.campaign_recipient_id == delivery.campaign_recipient_id,
                    CampaignDelivery.step_position > delivery.step_position,
                    ScheduledEmail.status.in_([
                        EmailStatus.pending.value,
                        EmailStatus.processing.value,
                    ]),
                )
                .limit(1)
            )
            if later_jobs is None:
                state.status = "completed"
        db.commit()
        logger.info("Sent job %s as Gmail message %s", job.id, send_result.message_id)


def sync_one_campaign_reply() -> bool:
    """Poll one active Gmail thread and stop that recipient's remaining sequence on reply."""
    with SessionLocal() as db:
        check_before = datetime.now(timezone.utc) - timedelta(seconds=30)
        state = db.execute(
            select(CampaignRecipientState)
            .where(
                CampaignRecipientState.status == "active",
                CampaignRecipientState.gmail_thread_id.is_not(None),
                or_(
                    CampaignRecipientState.last_checked_at.is_(None),
                    CampaignRecipientState.last_checked_at <= check_before,
                ),
            )
            .order_by(
                CampaignRecipientState.last_checked_at.asc().nullsfirst(),
                CampaignRecipientState.created_at,
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if state is None or state.gmail_thread_id is None:
            return False

        recipient = db.get(CampaignRecipient, state.campaign_recipient_id)
        if recipient is None:
            return False
        user = db.get(User, recipient.campaign.created_by_user_id)
        if user is None:
            return False
        try:
            replied_at = find_recipient_reply(db, user=user, recipient=recipient)
        except Exception:
            logger.exception("Failed to check Gmail thread %s for replies", state.gmail_thread_id)
            state.last_checked_at = datetime.now(timezone.utc)
            db.commit()
            return True

        state.last_checked_at = datetime.now(timezone.utc)
        if replied_at is not None:
            mark_recipient_replied(db, recipient_id=recipient.id, replied_at=replied_at)
            logger.info("Stopped campaign recipient %s after detecting a reply", recipient.id)
        db.commit()
        return True


def run_forever() -> None:
    settings = get_settings()
    Base.metadata.create_all(bind=engine)
    logger.info("Worker started; polling every %s seconds", settings.worker_poll_seconds)
    while True:
        job_id = claim_due_job()
        if job_id:
            process_job(job_id)
        else:
            checked_reply = sync_one_campaign_reply()
            if not checked_reply:
                time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run_forever()

