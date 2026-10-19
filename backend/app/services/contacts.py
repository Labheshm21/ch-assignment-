import csv
import io

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Contact, User, Workspace, WorkspaceMember
from ..schemas import ContactImportError, ContactImportResponse, ContactUpsertRequest


def normalize_email(email: str) -> str:
    return email.strip().casefold()


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
        if payload.tags is not None:
            contact.tags = payload.tags
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
    contact.tags = payload.tags or []
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

    added = 0
    updated = 0
    rejected = 0
    errors: list[ContactImportError] = []

    for index, raw_row in enumerate(reader, start=2):
        if index - 1 > maximum_rows:
            raise ValueError(f"CSV can contain at most {maximum_rows} contacts")
        row = {(key or "").strip().casefold(): (value or "").strip() for key, value in raw_row.items()}
        raw_tags = row.get("tags", "")
        try:
            payload = ContactUpsertRequest.model_validate(
                {
                    "email": row.get("email", ""),
                    "name": row.get("name") or None,
                    "company": row.get("company") or None,
                    "job_title": row.get("job_title") or row.get("title") or None,
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
    writer.writerow(["name", "email", "company", "job_title", "tags"])
    for contact in contacts:
        writer.writerow(
            [
                _safe_csv_cell(contact.name),
                _safe_csv_cell(contact.email),
                _safe_csv_cell(contact.company),
                _safe_csv_cell(contact.job_title),
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
