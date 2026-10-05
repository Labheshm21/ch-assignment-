import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from .config import get_settings
from .database import Base, SessionLocal, engine
from .models import EmailStatus, ScheduledEmail, User
from .services.gmail import send_gmail_message


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("chronos-worker")


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
        user = db.get(User, job.user_id)
        if user is None:
            job.status = EmailStatus.failed.value
            job.last_error = "Connected Gmail user no longer exists"
            db.commit()
            return

        try:
            message_id = send_gmail_message(
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
            else:
                job.status = EmailStatus.pending.value
                job.scheduled_at = datetime.now(timezone.utc) + timedelta(
                    minutes=2 ** (job.retry_count - 1)
                )
            db.commit()
            return

        job.status = EmailStatus.sent.value
        job.gmail_message_id = message_id
        job.sent_at = datetime.now(timezone.utc)
        job.locked_at = None
        job.last_error = None
        db.commit()
        logger.info("Sent job %s as Gmail message %s", job.id, message_id)


def run_forever() -> None:
    settings = get_settings()
    Base.metadata.create_all(bind=engine)
    logger.info("Worker started; polling every %s seconds", settings.worker_poll_seconds)
    while True:
        job_id = claim_due_job()
        if job_id:
            process_job(job_id)
        else:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run_forever()

