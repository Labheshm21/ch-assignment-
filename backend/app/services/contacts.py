import csv
import io
import re

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CAMPAIGN_BLOCKED_CONTACT_STAGES,
    CampaignDelivery,
    CampaignRecipient,
    CampaignRecipientState,
    Contact,
    ContactStage,
    EmailStatus,
    ScheduledEmail,
    User,
    Workspace,
    WorkspaceMember,
)
from ..schemas import ContactImportError, ContactImportResponse, ContactUpsertRequest


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def score_contact_fit(
    *,
    industry: str | None,
    use_case: str,
    technical_fit: str,
    buying_signal: str,
) -> tuple[int, str, str]:
    score = 0
    reasons: list[str] = []
    use_case_points = {
        "generative_video": 30,
        "gaming": 30,
        "robotics": 30,
        "media": 20,
        "avatars": 20,
    }.get(use_case, 0)
    if use_case_points:
        score += use_case_points
        reasons.append(f"{use_case.replace('_', ' ')} use case +{use_case_points}")

    technical_points = {"high": 30, "medium": 15, "low": 5}.get(technical_fit, 0)
    if technical_points:
        score += technical_points
        reasons.append(f"{technical_fit} technical fit +{technical_points}")

    signal_points = {"strong": 25, "medium": 15, "weak": 5}.get(buying_signal, 0)
    if signal_points:
        score += signal_points
        reasons.append(f"{buying_signal} buying signal +{signal_points}")

    industry_text = (industry or "").casefold()
    industry_tokens = set(re.findall(r"[a-z0-9]+", industry_text))
    target_industries = (
        "artificial intelligence",
        "gaming",
        "robotics",
        "media",
        "entertainment",
        "video",
        "animation",
        "vfx",
    )
    if industry_text and (
        "ai" in industry_tokens or any(keyword in industry_text for keyword in target_industries)
    ):
        score += 15
        reasons.append("target industry +15")

    score = min(score, 100)
    priority = "high" if score >= 75 else "medium" if score >= 50 else "low"
    return score, priority, "; ".join(reasons) or "No qualification signals yet"


def apply_contact_score(contact: Contact) -> None:
    contact.fit_score, contact.fit_priority, contact.fit_reason = score_contact_fit(
        industry=contact.industry,
        use_case=contact.use_case,
        technical_fit=contact.technical_fit,
        buying_signal=contact.buying_signal,
    )


def contact_campaign_blocked(contact: Contact) -> bool:
    return contact.stage in CAMPAIGN_BLOCKED_CONTACT_STAGES


def set_contact_stage(db: Session, *, contact: Contact, stage: str) -> int:
    contact.stage = stage
    if not contact_campaign_blocked(contact) and stage != ContactStage.new.value:
        db.flush()
        return 0

    recipient_ids = list(
        db.execute(
            select(CampaignRecipient.id).where(CampaignRecipient.contact_id == contact.id)
        ).scalars()
    )
    if not recipient_ids:
        db.flush()
        return 0

    states = db.execute(
        select(CampaignRecipientState).where(
            CampaignRecipientState.campaign_recipient_id.in_(recipient_ids),
            CampaignRecipientState.status == "active",
        )
    ).scalars()
    for state in states:
        state.status = "cancelled"

    pending_jobs = db.execute(
        select(ScheduledEmail)
        .join(CampaignDelivery, CampaignDelivery.scheduled_email_id == ScheduledEmail.id)
        .where(
            CampaignDelivery.campaign_recipient_id.in_(recipient_ids),
            ScheduledEmail.status == EmailStatus.pending.value,
        )
    ).scalars()
    cancelled = 0
    reason = stage.replace("_", " ")
    for pending_job in pending_jobs:
        pending_job.status = EmailStatus.cancelled.value
        pending_job.last_error = f"Cancelled because contact was marked {reason}"
        cancelled += 1
    db.flush()
    return cancelled


def get_or_create_personal_workspace(db: Session, user: User) -> Workspace:
    # Lock the user row so two first-time requests cannot create two personal workspaces.
    db.execute(select(User).where(User.id == user.id).with_for_update()).scalar_one()
    membership = db.execute(
        select(WorkspaceMember)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(WorkspaceMember.created_at.asc())
    ).scalars().first()
    if membership is not None:
        return membership.workspace

    display_name = user.email.split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
    display_name = display_name.title() or "My"
    workspace = Workspace(name=f"{display_name}'s Workspace")
    db.add(workspace)
    db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
    db.flush()
    return workspace


def upsert_contact(
    db: Session,
    *,
    workspace_id: str,
    payload: ContactUpsertRequest,
) -> tuple[Contact, bool]:
    normalized = normalize_email(str(payload.email))
    contact = db.execute(
        select(Contact).where(
            Contact.workspace_id == workspace_id,
            Contact.normalized_email == normalized,
        )
    ).scalar_one_or_none()
    created = contact is None
    if contact is None:
        contact = Contact(
            workspace_id=workspace_id,
            email=str(payload.email).strip(),
            normalized_email=normalized,
            name=payload.name,
            company=payload.company,
            job_title=payload.job_title,
            website=str(payload.website) if payload.website is not None else None,
            industry=payload.industry,
            company_size=payload.company_size,
            lead_source=payload.lead_source,
            use_case=payload.use_case or "unknown",
            technical_fit=payload.technical_fit or "unknown",
            buying_signal=payload.buying_signal or "none",
            research_notes=payload.research_notes,
            tags=payload.tags or [],
        )
        db.add(contact)
    else:
        contact.email = str(payload.email).strip()
        if payload.name is not None:
            contact.name = payload.name
        if payload.company is not None:
            contact.company = payload.company
        if payload.job_title is not None:
            contact.job_title = payload.job_title
        if payload.website is not None:
            contact.website = str(payload.website)
        if payload.industry is not None:
            contact.industry = payload.industry
        if payload.company_size is not None:
            contact.company_size = payload.company_size
        if payload.lead_source is not None:
            contact.lead_source = payload.lead_source
        if payload.use_case is not None:
            contact.use_case = payload.use_case
        if payload.technical_fit is not None:
            contact.technical_fit = payload.technical_fit
        if payload.buying_signal is not None:
            contact.buying_signal = payload.buying_signal
        if payload.research_notes is not None:
            contact.research_notes = payload.research_notes
        if payload.tags is not None:
            contact.tags = payload.tags
    apply_contact_score(contact)
    db.flush()
    return contact, created


def update_contact(
    db: Session,
    *,
    contact: Contact,
    payload: ContactUpsertRequest,
) -> Contact:
    normalized = normalize_email(str(payload.email))
    duplicate = db.execute(
        select(Contact).where(
            Contact.workspace_id == contact.workspace_id,
            Contact.normalized_email == normalized,
            Contact.id != contact.id,
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise ValueError("Another contact already uses that email address")

    contact.email = str(payload.email).strip()
    contact.normalized_email = normalized
    contact.name = payload.name
    contact.company = payload.company
    contact.job_title = payload.job_title
    contact.website = str(payload.website) if payload.website is not None else None
    contact.industry = payload.industry
    contact.company_size = payload.company_size
    contact.lead_source = payload.lead_source
    contact.use_case = payload.use_case or "unknown"
    contact.technical_fit = payload.technical_fit or "unknown"
    contact.buying_signal = payload.buying_signal or "none"
    contact.research_notes = payload.research_notes
    contact.tags = payload.tags or []
    apply_contact_score(contact)
    db.flush()
    return contact


def import_contacts_csv(
    db: Session,
    *,
    workspace_id: str,
    content: bytes,
    maximum_rows: int = 1_000,
) -> ContactImportResponse:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV must be UTF-8 encoded") from exc

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("CSV is empty or has no header row")
    normalized_headers = {header.strip().casefold() for header in reader.fieldnames if header}
    if "email" not in normalized_headers:
        raise ValueError("CSV must include an email column")
    is_apollo_export = bool(
        {"apollo contact id", "apollo person id", "company name for emails"}
        & normalized_headers
    )

    added = 0
    updated = 0
    rejected = 0
    errors: list[ContactImportError] = []

    for index, raw_row in enumerate(reader, start=2):
        if index - 1 > maximum_rows:
            raise ValueError(f"CSV can contain at most {maximum_rows} contacts")
        row = {(key or "").strip().casefold(): (value or "").strip() for key, value in raw_row.items()}
        raw_tags = row.get("tags", "")
        full_name = row.get("name") or " ".join(
            part for part in (row.get("first name"), row.get("last name")) if part
        ).strip()
        try:
            payload = ContactUpsertRequest.model_validate(
                {
                    "email": row.get("email", ""),
                    "name": full_name or None,
                    "company": row.get("company")
                    or row.get("company name")
                    or row.get("company name for emails")
                    or None,
                    "job_title": row.get("job_title") or row.get("title") or None,
                    "website": row.get("website")
                    or row.get("company_website")
                    or row.get("company website")
                    or row.get("organization_website_url")
                    or None,
                    "industry": row.get("industry") or None,
                    "company_size": row.get("company_size")
                    or row.get("company size")
                    or row.get("organization_num_employees")
                    or row.get("# employees")
                    or None,
                    "lead_source": row.get("lead_source")
                    or row.get("source")
                    or ("Apollo" if is_apollo_export else None),
                    "use_case": row.get("use_case") or None,
                    "technical_fit": row.get("technical_fit") or None,
                    "buying_signal": row.get("buying_signal") or None,
                    "research_notes": row.get("research_notes") or row.get("notes") or None,
                    "tags": [tag.strip() for tag in raw_tags.replace(",", ";").split(";") if tag.strip()]
                    if raw_tags
                    else None,
                }
            )
        except ValidationError as exc:
            rejected += 1
            if len(errors) < 20:
                errors.append(ContactImportError(row=index, message=exc.errors()[0]["msg"]))
            continue

        _, created = upsert_contact(db, workspace_id=workspace_id, payload=payload)
        if created:
            added += 1
        else:
            updated += 1

    return ContactImportResponse(added=added, updated=updated, rejected=rejected, errors=errors)


def export_contacts_csv(contacts: list[Contact]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "name",
            "email",
            "company",
            "job_title",
            "website",
            "industry",
            "company_size",
            "lead_source",
            "use_case",
            "technical_fit",
            "buying_signal",
            "research_notes",
            "fit_score",
            "fit_priority",
            "fit_reason",
            "stage",
            "tags",
        ]
    )
    for contact in contacts:
        writer.writerow(
            [
                _safe_csv_cell(contact.name),
                _safe_csv_cell(contact.email),
                _safe_csv_cell(contact.company),
                _safe_csv_cell(contact.job_title),
                _safe_csv_cell(contact.website),
                _safe_csv_cell(contact.industry),
                _safe_csv_cell(contact.company_size),
                _safe_csv_cell(contact.lead_source),
                _safe_csv_cell(contact.use_case),
                _safe_csv_cell(contact.technical_fit),
                _safe_csv_cell(contact.buying_signal),
                _safe_csv_cell(contact.research_notes),
                contact.fit_score,
                _safe_csv_cell(contact.fit_priority),
                _safe_csv_cell(contact.fit_reason),
                _safe_csv_cell(contact.stage),
                _safe_csv_cell(";".join(contact.tags)),
            ]
        )
    return output.getvalue()


def _safe_csv_cell(value: str | None) -> str:
    text = value or ""
    # Prevent spreadsheet applications from interpreting contact data as formulas.
    if text.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text
